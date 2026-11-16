"""Real-PostgreSQL integration tests. Run with TEST_DATABASE_URL set."""

import os
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from fastapi.testclient import TestClient
from psycopg.conninfo import conninfo_to_dict

from checkstock import db, inventory
from checkstock.api import app
from checkstock.seed import seed


class InventoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = os.environ.get("TEST_DATABASE_URL")
        if not url:
            raise unittest.SkipTest("Set TEST_DATABASE_URL to a disposable PostgreSQL database")
        if not conninfo_to_dict(url).get("dbname", "").endswith("_test"):
            raise RuntimeError("TEST_DATABASE_URL must point to a database named *_test")
        cls.previous_url = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = url
        db.migrate()

    @classmethod
    def tearDownClass(cls):
        if cls.previous_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = cls.previous_url

    def setUp(self):
        with db.connect() as conn:
            conn.execute("TRUNCATE movement_events, operations, balances, products, warehouses RESTART IDENTITY CASCADE")
        seed()
        with db.connect() as conn:
            self.p = {r["sku"]: r["id"] for r in conn.execute("SELECT id, sku FROM products")}
            self.w = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM warehouses")}

    def balance(self, sku, warehouse):
        with db.connect() as conn:
            return conn.execute(
                "SELECT physical, reserved_fbs, physical - reserved_fbs AS available "
                "FROM balances WHERE product_id = %s AND warehouse_id = %s",
                (self.p[sku], self.w[warehouse]),
            ).fetchone()

    def event_count(self):
        with db.connect() as conn:
            return conn.execute("SELECT count(*) AS n FROM movement_events").fetchone()["n"]

    def test_reserve_then_cancel(self):
        op = inventory.reserve_fbs(self.p["SKU-101"], self.w["Север"], 3, "reserve-1")
        self.assertEqual(self.balance("SKU-101", "Север"), {"physical": 10, "reserved_fbs": 3, "available": 7})
        inventory.transition(op["id"], "cancel_fbs")
        self.assertEqual(self.balance("SKU-101", "Север"), {"physical": 10, "reserved_fbs": 0, "available": 10})
        self.assertEqual(self.event_count(), 2)
        inventory.transition(op["id"], "cancel_fbs")
        self.assertEqual(self.event_count(), 2)

    def test_reserve_then_ship_fbs(self):
        op = inventory.reserve_fbs(self.p["SKU-101"], self.w["Север"], 4, "reserve-2")
        inventory.transition(op["id"], "ship_fbs")
        self.assertEqual(self.balance("SKU-101", "Север"), {"physical": 6, "reserved_fbs": 0, "available": 6})
        self.assertEqual(inventory.stock_snapshot()["in_transit"][0]["quantity"], 4)
        inventory.transition(op["id"], "ship_fbs")
        self.assertEqual(self.event_count(), 2)

    def test_transfer_receive_and_repeat(self):
        op = inventory.send_transfer(self.p["SKU-101"], self.w["Север"], self.w["Юг"], 3, "transfer-1")
        self.assertEqual(self.balance("SKU-101", "Север")["physical"], 7)
        self.assertEqual(self.balance("SKU-101", "Юг")["physical"], 6)
        self.assertEqual(inventory.stock_snapshot()["in_transit"][0]["quantity"], 3)
        inventory.transition(op["id"], "receive_transfer")
        inventory.transition(op["id"], "receive_transfer")
        self.assertEqual(self.balance("SKU-101", "Юг")["physical"], 9)
        self.assertEqual(inventory.stock_snapshot()["in_transit"], [])
        self.assertEqual(self.event_count(), 2)

    def test_transit_aggregates_without_double_counting(self):
        first = inventory.send_transfer(self.p["SKU-101"], self.w["Север"], self.w["Юг"], 2, "aggregate-1")
        inventory.send_transfer(self.p["SKU-101"], self.w["Север"], self.w["Юг"], 3, "aggregate-2")
        snapshot = inventory.stock_snapshot()
        transit = [r for r in snapshot["in_transit"] if r["sku"] == "SKU-101"]
        self.assertEqual(len(transit), 1)
        self.assertEqual(transit[0]["quantity"], 5)
        physical = sum(r["physical"] for r in snapshot["balances"] if r["sku"] == "SKU-101")
        self.assertEqual(physical + transit[0]["quantity"], 16)
        inventory.transition(first["id"], "receive_transfer")
        snapshot = inventory.stock_snapshot()
        physical = sum(r["physical"] for r in snapshot["balances"] if r["sku"] == "SKU-101")
        self.assertEqual(physical + snapshot["in_transit"][0]["quantity"], 16)

    def test_fbo_shipment_and_receipt(self):
        op = inventory.send_fbo(self.p["SKU-101"], self.w["Север"], 2, "fbo-1")
        self.assertEqual(self.balance("SKU-101", "Север")["physical"], 8)
        self.assertEqual(inventory.stock_snapshot()["in_transit"][0]["kind"], "fbo")
        inventory.transition(op["id"], "receive_fbo")
        inventory.transition(op["id"], "receive_fbo")
        self.assertEqual(self.balance("SKU-101", "Север")["physical"], 8)
        self.assertEqual(inventory.stock_snapshot()["in_transit"], [])
        self.assertEqual(self.event_count(), 2)

    def test_invalid_transition_changes_nothing(self):
        op = inventory.reserve_fbs(self.p["SKU-101"], self.w["Север"], 2, "transition-error")
        inventory.transition(op["id"], "ship_fbs")
        before = self.balance("SKU-101", "Север")
        with self.assertRaisesRegex(inventory.InventoryError, "другим действием"):
            inventory.transition(op["id"], "cancel_fbs")
        self.assertEqual(self.balance("SKU-101", "Север"), before)
        self.assertEqual(self.event_count(), 2)
        with db.connect() as conn:
            self.assertEqual(conn.execute("SELECT status FROM operations WHERE id = %s", (op["id"],)).fetchone()["status"], "shipped")

    def test_shortage_on_selected_warehouse_rolls_back(self):
        before = self.balance("SKU-102", "Юг")
        with self.assertRaisesRegex(inventory.InventoryError, "доступно 0"):
            inventory.reserve_fbs(self.p["SKU-102"], self.w["Юг"], 1, "shortage")
        self.assertEqual(self.balance("SKU-102", "Юг"), before)
        self.assertEqual(self.event_count(), 0)
        with db.connect() as conn:
            self.assertEqual(conn.execute("SELECT count(*) AS n FROM operations").fetchone()["n"], 0)

    def test_idempotency_replay_and_conflict(self):
        first = inventory.receive_stock(self.p["SKU-101"], self.w["Север"], 2, "same-key")
        second = inventory.receive_stock(self.p["SKU-101"], self.w["Север"], 2, "same-key")
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(self.balance("SKU-101", "Север")["physical"], 12)
        self.assertEqual(self.event_count(), 1)
        for product, quantity, warehouse in ((self.p["SKU-102"], 2, self.w["Север"]),
                                             (self.p["SKU-101"], 3, self.w["Север"]),
                                             (self.p["SKU-101"], 2, self.w["Юг"])):
            with self.assertRaisesRegex(inventory.InventoryError, "другими параметрами"):
                inventory.receive_stock(product, warehouse, quantity, "same-key")
        self.assertEqual(self.event_count(), 1)
        self.assertEqual(self.balance("SKU-101", "Север")["physical"], 12)

    def test_archive_keeps_stock_and_allows_transfer_out(self):
        inventory.archive_warehouse(self.w["Север"])
        rows = inventory.stock_snapshot()["balances"]
        self.assertTrue(any(r["sku"] == "SKU-101" and r["warehouse_name"] == "Север"
                            and r["physical"] == 10 and r["archived_at"] for r in rows))
        with self.assertRaisesRegex(inventory.InventoryError, "архивирован"):
            inventory.reserve_fbs(self.p["SKU-101"], self.w["Север"], 1, "new-reserve")
        with self.assertRaisesRegex(inventory.InventoryError, "архивирован"):
            inventory.receive_stock(self.p["SKU-101"], self.w["Север"], 1, "new-receipt")
        with self.assertRaisesRegex(inventory.InventoryError, "архивирован"):
            inventory.send_transfer(self.p["SKU-101"], self.w["Юг"], self.w["Север"], 1, "to-archive")
        op = inventory.send_transfer(self.p["SKU-101"], self.w["Север"], self.w["Юг"], 2, "leave-archive")
        inventory.transition(op["id"], "receive_transfer")
        self.assertEqual(self.balance("SKU-101", "Север")["physical"], 8)
        self.assertEqual(self.balance("SKU-101", "Юг")["physical"], 8)

    def test_restore_preserves_inventory_and_reenables_operations(self):
        warehouse = self.w["Север"]
        inventory.reserve_fbs(self.p["SKU-101"], warehouse, 2, "before-restore")
        inventory.send_transfer(self.p["SKU-101"], warehouse, self.w["Юг"], 1, "pending-restore")
        before = inventory.stock_snapshot()
        history = inventory.list_history()
        pending = inventory.catalog()["open_operations"]
        inventory.archive_warehouse(warehouse)
        with TestClient(app) as client:
            first = client.post(f"/api/warehouses/{warehouse}/restore")
            second = client.post(f"/api/warehouses/{warehouse}/restore")
        self.assertEqual(first.status_code, 200)
        self.assertIsNone(first.json()["archived_at"])
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json(), second.json())
        self.assertEqual(inventory.stock_snapshot(), before)
        self.assertEqual(inventory.list_history(), history)
        self.assertEqual(inventory.catalog()["open_operations"], pending)
        inventory.receive_stock(self.p["SKU-101"], warehouse, 3, "restored-receipt")
        inventory.reserve_fbs(self.p["SKU-101"], warehouse, 1, "restored-reserve")
        transfer = inventory.send_transfer(self.p["SKU-101"], self.w["Юг"], warehouse, 1, "restored-target")
        inventory.transition(transfer["id"], "receive_transfer")
        self.assertEqual(self.balance("SKU-101", "Север"), {"physical": 13, "reserved_fbs": 3, "available": 10})
        self.assertEqual(self.event_count(), 6)

    def test_restore_missing_warehouse_changes_nothing(self):
        before = inventory.stock_snapshot()
        catalog = inventory.catalog()
        history = inventory.list_history()
        with TestClient(app) as client:
            response = client.post("/api/warehouses/999999/restore")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(inventory.stock_snapshot(), before)
        self.assertEqual(inventory.catalog(), catalog)
        self.assertEqual(inventory.list_history(), history)

    def test_started_operations_can_finish_after_archive(self):
        reserve = inventory.reserve_fbs(self.p["SKU-101"], self.w["Север"], 2, "before-archive")
        transfer = inventory.send_transfer(self.p["SKU-101"], self.w["Север"], self.w["Юг"], 2, "to-south")
        inventory.archive_warehouse(self.w["Север"])
        inventory.archive_warehouse(self.w["Юг"])
        inventory.transition(reserve["id"], "ship_fbs")
        inventory.transition(transfer["id"], "receive_transfer")
        self.assertEqual(self.balance("SKU-101", "Юг")["physical"], 8)
        self.assertEqual(self.balance("SKU-101", "Север")["physical"], 6)

    def test_concurrent_reservations_use_separate_transactions(self):
        barrier = threading.Barrier(2)

        def worker(key):
            barrier.wait()
            try:
                inventory.reserve_fbs(self.p["SKU-101"], self.w["Север"], 7, key)
                return "ok"
            except inventory.InventoryError:
                return "shortage"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(worker, ("concurrent-1", "concurrent-2")))
        self.assertCountEqual(results, ["ok", "shortage"])
        self.assertEqual(self.balance("SKU-101", "Север"), {"physical": 10, "reserved_fbs": 7, "available": 3})
        self.assertEqual(self.event_count(), 1)

    def test_concurrent_replay_creates_one_receipt(self):
        barrier = threading.Barrier(2)

        def worker(_):
            barrier.wait()
            return inventory.receive_stock(self.p["SKU-101"], self.w["Север"], 2, "shared-key")["id"]

        with ThreadPoolExecutor(max_workers=2) as pool:
            ids = list(pool.map(worker, range(2)))
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(self.balance("SKU-101", "Север")["physical"], 12)
        self.assertEqual(self.event_count(), 1)

    def test_history_cursor_does_not_repeat_events(self):
        for number in range(3):
            inventory.receive_stock(self.p["SKU-101"], self.w["Север"], 1, f"history-{number}")
        first = inventory.list_history(limit=2)
        second = inventory.list_history(limit=2, before_id=first["next_before_id"])
        self.assertEqual(len(first["items"]), 2)
        self.assertEqual(len(second["items"]), 1)
        self.assertEqual(len({e["id"] for e in first["items"] + second["items"]}), 3)

    def test_archiving_race_rejects_new_target(self):
        locked = threading.Event()
        release = threading.Event()
        real_lock = inventory._warehouses

        def controlled_lock(conn, ids, **kwargs):
            result = real_lock(conn, ids, **kwargs)
            if threading.current_thread().name == "archive-thread":
                locked.set()
                if not release.wait(timeout=10):
                    raise TimeoutError("archive race test timed out")
            return result

        def archive():
            inventory.archive_warehouse(self.w["Юг"])

        def send():
            try:
                inventory.send_transfer(self.p["SKU-101"], self.w["Север"], self.w["Юг"], 1, "racing")
                return "created"
            except inventory.InventoryError:
                return "rejected"

        with patch.object(inventory, "_warehouses", controlled_lock):
            archive_thread = threading.Thread(target=archive, name="archive-thread")
            archive_thread.start()
            self.assertTrue(locked.wait(timeout=10))
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(send)
                release.set()
                result = future.result(timeout=10)
            archive_thread.join(timeout=10)
        self.assertEqual(result, "rejected")
        self.assertFalse(archive_thread.is_alive())
        with db.connect() as conn:
            self.assertIsNotNone(conn.execute("SELECT archived_at FROM warehouses WHERE id = %s", (self.w["Юг"],)).fetchone()["archived_at"])
        self.assertEqual(self.balance("SKU-101", "Север")["physical"], 10)
        self.assertEqual(self.event_count(), 0)

    def test_stock_uses_one_consistent_snapshot(self):
        real_connect = inventory.connect
        north, south, product = self.w["Север"], self.w["Юг"], self.p["SKU-101"]

        class InterceptConnection:
            def __init__(self):
                self.conn = real_connect()
                self.fired = False

            def __enter__(self):
                self.conn.__enter__()
                return self

            def __exit__(self, *args):
                return self.conn.__exit__(*args)

            def execute(self, sql, params=()):
                if "FROM operations o JOIN products" in sql and not self.fired:
                    self.fired = True
                    with real_connect() as other:
                        other.execute("UPDATE balances SET physical = physical - 2 WHERE product_id = %s AND warehouse_id = %s", (product, north))
                        other.execute("INSERT INTO operations (kind, product_id, quantity, source_warehouse_id, target_warehouse_id, status) VALUES ('transfer', %s, 2, %s, %s, 'pending')", (product, north, south))
                return self.conn.execute(sql, params)

        with patch.object(inventory, "connect", InterceptConnection):
            snapshot = inventory.stock_snapshot()
        amount = sum(r["physical"] for r in snapshot["balances"] if r["sku"] == "SKU-101")
        amount += sum(r["quantity"] for r in snapshot["in_transit"] if r["sku"] == "SKU-101")
        self.assertEqual(amount, 16)
        self.assertEqual(snapshot["in_transit"], [])
        fresh = inventory.stock_snapshot()
        self.assertEqual(sum(r["physical"] for r in fresh["balances"] if r["sku"] == "SKU-101"), 14)
        self.assertEqual(fresh["in_transit"][0]["quantity"], 2)

    def test_http_route_and_page_smoke(self):
        with TestClient(app) as client:
            self.assertEqual(client.get("/").status_code, 200)
            self.assertIn("Остатки по складам", client.get("/").text)
            self.assertEqual(len(client.get("/api/stock").json()["balances"]), 6)
            response = client.post("/api/fbs/reservations", json={"product_id": self.p["SKU-102"],
                "warehouse_id": self.w["Юг"], "quantity": 1, "idempotency_key": "http-shortage"})
            self.assertEqual(response.status_code, 409)
            self.assertIn("доступно 0", response.json()["detail"])

    def test_http_receipt_updates_stock_and_history(self):
        body = {"product_id": self.p["SKU-103"], "warehouse_id": self.w["Юг"],
                "quantity": 2, "idempotency_key": "http-receipt"}
        with TestClient(app) as client:
            first = client.post("/api/receipts", json=body)
            second = client.post("/api/receipts", json=body)
            self.assertEqual(first.status_code, 201)
            self.assertEqual(second.json()["id"], first.json()["id"])
            rows = client.get("/api/stock").json()["balances"]
            south = next(r for r in rows if r["sku"] == "SKU-103" and r["warehouse_name"] == "Юг")
            self.assertEqual((south["physical"], south["available"]), (6, 6))
            events = client.get("/api/history").json()["items"]
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["action"], "receipt")
