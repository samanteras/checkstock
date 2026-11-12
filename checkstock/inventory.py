"""Transactional inventory rules. Each public command commits all changes together."""

import json
from typing import Any

from psycopg.errors import UniqueViolation

from checkstock.db import connect


class InventoryError(Exception):
    def __init__(self, message: str, status_code: int = 409):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def _row(conn, query: str, params: tuple = ()) -> dict:
    result = conn.execute(query, params).fetchone()
    if result is None:
        raise InventoryError("Объект не найден", 404)
    return result


def _product(conn, product_id: int):
    return _row(conn, "SELECT id FROM products WHERE id = %s", (product_id,))


def _warehouses(conn, ids: list[int], *, active: set[int] | None = None):
    active = active or set()
    for warehouse_id in sorted(set(ids)):
        row = _row(conn, "SELECT id, archived_at FROM warehouses WHERE id = %s FOR UPDATE", (warehouse_id,))
        if warehouse_id in active and row["archived_at"] is not None:
            raise InventoryError("Склад архивирован")


def _balance(conn, product_id: int, warehouse_id: int):
    conn.execute(
        "INSERT INTO balances (product_id, warehouse_id) VALUES (%s, %s) "
        "ON CONFLICT (product_id, warehouse_id) DO NOTHING",
        (product_id, warehouse_id),
    )
    return _row(
        conn,
        "SELECT physical, reserved_fbs FROM balances "
        "WHERE product_id = %s AND warehouse_id = %s FOR UPDATE",
        (product_id, warehouse_id),
    )


def _take_available(conn, product_id: int, warehouse_id: int, quantity: int):
    balance = _balance(conn, product_id, warehouse_id)
    available = balance["physical"] - balance["reserved_fbs"]
    if available < quantity:
        raise InventoryError(f"На выбранном складе доступно {available}; нужно {quantity}")


def _event(conn, operation_id: int, action: str, product_id: int, quantity: int,
           source: int | None, target: int | None):
    conn.execute(
        "INSERT INTO movement_events "
        "(operation_id, action, product_id, quantity, source_warehouse_id, target_warehouse_id) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (operation_id, action, product_id, quantity, source, target),
    )


def _keyed(conn, key: str, fingerprint: dict[str, Any]):
    if not key or not key.strip() or len(key) > 128:
        raise InventoryError("Нужен idempotency_key длиной до 128 символов", 400)
    encoded = json.dumps(fingerprint, sort_keys=True, separators=(",", ":"))
    conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s)::bigint)", (key,))
    existing = conn.execute("SELECT * FROM operations WHERE idempotency_key = %s", (key,)).fetchone()
    if existing:
        if existing["fingerprint"] != encoded:
            raise InventoryError("idempotency_key уже использован с другими параметрами")
        return existing, encoded
    return None, encoded


def _create(conn, kind: str, product_id: int, quantity: int, source: int | None,
            target: int | None, status: str, key: str, fingerprint: str):
    return _row(
        conn,
        "INSERT INTO operations (kind, product_id, quantity, source_warehouse_id, "
        "target_warehouse_id, status, idempotency_key, fingerprint) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING *",
        (kind, product_id, quantity, source, target, status, key, fingerprint),
    )


def _validate_quantity(quantity: int):
    if quantity <= 0:
        raise InventoryError("Количество должно быть положительным", 400)


def receive_stock(product_id: int, warehouse_id: int, quantity: int, key: str):
    _validate_quantity(quantity)
    with connect() as conn:
        existing, fingerprint = _keyed(conn, key, dict(kind="receipt", product=product_id,
                                                     quantity=quantity, target=warehouse_id))
        if existing:
            return existing
        _product(conn, product_id)
        _warehouses(conn, [warehouse_id], active={warehouse_id})
        _balance(conn, product_id, warehouse_id)
        operation = _create(conn, "receipt", product_id, quantity, None, warehouse_id,
                            "completed", key, fingerprint)
        conn.execute("UPDATE balances SET physical = physical + %s WHERE product_id = %s AND warehouse_id = %s",
                     (quantity, product_id, warehouse_id))
        _event(conn, operation["id"], "receipt", product_id, quantity, None, warehouse_id)
        return operation


def reserve_fbs(product_id: int, warehouse_id: int, quantity: int, key: str):
    _validate_quantity(quantity)
    with connect() as conn:
        existing, fingerprint = _keyed(conn, key, dict(kind="fbs", product=product_id,
                                                     quantity=quantity, source=warehouse_id))
        if existing:
            return existing
        _product(conn, product_id)
        _warehouses(conn, [warehouse_id], active={warehouse_id})
        _take_available(conn, product_id, warehouse_id, quantity)
        operation = _create(conn, "fbs", product_id, quantity, warehouse_id, None,
                            "pending", key, fingerprint)
        conn.execute("UPDATE balances SET reserved_fbs = reserved_fbs + %s "
                     "WHERE product_id = %s AND warehouse_id = %s",
                     (quantity, product_id, warehouse_id))
        _event(conn, operation["id"], "reserve_fbs", product_id, quantity, warehouse_id, None)
        return operation


def send_transfer(product_id: int, source_id: int, target_id: int, quantity: int, key: str):
    _validate_quantity(quantity)
    if source_id == target_id:
        raise InventoryError("Склады отправки и получения должны различаться", 400)
    with connect() as conn:
        existing, fingerprint = _keyed(conn, key, dict(kind="transfer", product=product_id,
                                                     quantity=quantity, source=source_id, target=target_id))
        if existing:
            return existing
        _product(conn, product_id)
        _warehouses(conn, [source_id, target_id], active={target_id})
        _take_available(conn, product_id, source_id, quantity)
        operation = _create(conn, "transfer", product_id, quantity, source_id, target_id,
                            "pending", key, fingerprint)
        conn.execute("UPDATE balances SET physical = physical - %s "
                     "WHERE product_id = %s AND warehouse_id = %s", (quantity, product_id, source_id))
        _event(conn, operation["id"], "send_transfer", product_id, quantity, source_id, target_id)
        return operation


def send_fbo(product_id: int, warehouse_id: int, quantity: int, key: str):
    _validate_quantity(quantity)
    with connect() as conn:
        existing, fingerprint = _keyed(conn, key, dict(kind="fbo", product=product_id,
                                                     quantity=quantity, source=warehouse_id))
        if existing:
            return existing
        _product(conn, product_id)
        _warehouses(conn, [warehouse_id], active={warehouse_id})
        _take_available(conn, product_id, warehouse_id, quantity)
        operation = _create(conn, "fbo", product_id, quantity, warehouse_id, None,
                            "pending", key, fingerprint)
        conn.execute("UPDATE balances SET physical = physical - %s "
                     "WHERE product_id = %s AND warehouse_id = %s", (quantity, product_id, warehouse_id))
        _event(conn, operation["id"], "send_fbo", product_id, quantity, warehouse_id, None)
        return operation


def transition(operation_id: int, action: str):
    transitions = {
        "ship_fbs": ("fbs", "pending", "shipped"),
        "cancel_fbs": ("fbs", "pending", "cancelled"),
        "receive_transfer": ("transfer", "pending", "completed"),
        "receive_fbo": ("fbo", "pending", "completed"),
    }
    kind, before, after = transitions[action]
    with connect() as conn:
        operation = _row(conn, "SELECT * FROM operations WHERE id = %s FOR UPDATE", (operation_id,))
        if operation["kind"] != kind:
            raise InventoryError("Неверный тип операции")
        if operation["status"] == after:
            return operation
        if operation["status"] != before:
            raise InventoryError("Операция уже завершена другим действием")
        product_id, quantity = operation["product_id"], operation["quantity"]
        source, target = operation["source_warehouse_id"], operation["target_warehouse_id"]
        if action in ("ship_fbs", "cancel_fbs"):
            _balance(conn, product_id, source)
            if action == "ship_fbs":
                conn.execute("UPDATE balances SET physical = physical - %s, "
                             "reserved_fbs = reserved_fbs - %s "
                             "WHERE product_id = %s AND warehouse_id = %s",
                             (quantity, quantity, product_id, source))
            else:
                conn.execute("UPDATE balances SET reserved_fbs = reserved_fbs - %s "
                             "WHERE product_id = %s AND warehouse_id = %s", (quantity, product_id, source))
        elif action == "receive_transfer":
            _balance(conn, product_id, target)
            conn.execute("UPDATE balances SET physical = physical + %s "
                         "WHERE product_id = %s AND warehouse_id = %s", (quantity, product_id, target))
        operation = _row(conn, "UPDATE operations SET status = %s, completed_at = now() "
                         "WHERE id = %s RETURNING *", (after, operation_id))
        _event(conn, operation_id, action, product_id, quantity, source, target)
        return operation


def add_warehouse(name: str):
    name = name.strip()
    if not name:
        raise InventoryError("Название склада не может быть пустым", 400)
    with connect() as conn:
        row = conn.execute("INSERT INTO warehouses (name) VALUES (%s) "
                           "ON CONFLICT (name) DO NOTHING RETURNING *", (name,)).fetchone()
        if not row:
            raise InventoryError("Склад с таким названием уже существует")
        return row


def rename_warehouse(warehouse_id: int, name: str):
    name = name.strip()
    if not name:
        raise InventoryError("Название склада не может быть пустым", 400)
    try:
        with connect() as conn:
            _warehouses(conn, [warehouse_id])
            return _row(conn, "UPDATE warehouses SET name = %s WHERE id = %s RETURNING *", (name, warehouse_id))
    except UniqueViolation as exc:
        raise InventoryError("Склад с таким названием уже существует") from exc


def archive_warehouse(warehouse_id: int):
    with connect() as conn:
        _warehouses(conn, [warehouse_id])
        return _row(conn, "UPDATE warehouses SET archived_at = COALESCE(archived_at, now()) "
                    "WHERE id = %s RETURNING *", (warehouse_id,))


def stock_snapshot():
    """Both reads use one MVCC snapshot, including concurrent transfer commits."""
    with connect() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        balances = conn.execute(
            "SELECT p.id AS product_id, p.sku, p.name AS product_name, w.id AS warehouse_id, "
            "w.name AS warehouse_name, w.archived_at, COALESCE(b.physical, 0) AS physical, "
            "COALESCE(b.reserved_fbs, 0) AS reserved_fbs, "
            "COALESCE(b.physical, 0) - COALESCE(b.reserved_fbs, 0) AS available "
            "FROM products p CROSS JOIN warehouses w "
            "LEFT JOIN balances b ON b.product_id = p.id AND b.warehouse_id = w.id "
            "ORDER BY p.sku, w.id"
        ).fetchall()
        transit = conn.execute(
            "SELECT o.product_id, p.sku, p.name AS product_name, o.kind, "
            "SUM(o.quantity) AS quantity, o.source_warehouse_id, src.name AS source_name, "
            "o.target_warehouse_id, dst.name AS target_name "
            "FROM operations o JOIN products p ON p.id = o.product_id "
            "LEFT JOIN warehouses src ON src.id = o.source_warehouse_id "
            "LEFT JOIN warehouses dst ON dst.id = o.target_warehouse_id "
            "WHERE (o.kind IN ('transfer', 'fbo') AND o.status = 'pending') "
            "OR (o.kind = 'fbs' AND o.status = 'shipped') "
            "GROUP BY o.product_id, p.sku, p.name, o.kind, o.source_warehouse_id, "
            "src.name, o.target_warehouse_id, dst.name "
            "ORDER BY p.sku, o.kind"
        ).fetchall()
        return {"balances": balances, "in_transit": transit}


def list_history(limit: int = 50, before_id: int | None = None):
    with connect() as conn:
        rows = conn.execute(
            "SELECT e.id, e.operation_id, e.action, e.quantity, e.created_at, "
            "p.sku, p.name AS product_name, "
            "CASE WHEN e.action = 'cancel_fbs' THEN 'Резерв FBS' "
            "WHEN e.action = 'receipt' THEN 'Внешний приход' ELSE src.name END AS source_name, "
            "CASE WHEN e.action = 'reserve_fbs' THEN 'Резерв FBS' "
            "WHEN e.action = 'cancel_fbs' THEN src.name "
            "WHEN e.action = 'ship_fbs' THEN 'Клиент FBS' "
            "WHEN e.action IN ('send_fbo', 'receive_fbo') THEN 'Маркетплейс FBO' "
            "ELSE dst.name END AS target_name, "
            "CASE WHEN e.action = 'cancel_fbs' THEN NULL ELSE src.archived_at END AS source_archived_at, "
            "CASE WHEN e.action = 'cancel_fbs' THEN src.archived_at ELSE dst.archived_at END AS target_archived_at "
            "FROM movement_events e JOIN products p ON p.id = e.product_id "
            "LEFT JOIN warehouses src ON src.id = e.source_warehouse_id "
            "LEFT JOIN warehouses dst ON dst.id = e.target_warehouse_id "
            "WHERE (%s::bigint IS NULL OR e.id < %s) ORDER BY e.id DESC LIMIT %s",
            (before_id, before_id, limit),
        ).fetchall()
        return {"items": rows, "next_before_id": rows[-1]["id"] if len(rows) == limit else None}


def catalog():
    with connect() as conn:
        return {
            "products": conn.execute("SELECT id, sku, name FROM products ORDER BY sku").fetchall(),
            "warehouses": conn.execute("SELECT id, name, archived_at FROM warehouses ORDER BY id").fetchall(),
            "open_operations": conn.execute(
                "SELECT o.id, o.kind, o.status, o.quantity, p.sku, "
                "src.name AS source_name, dst.name AS target_name "
                "FROM operations o JOIN products p ON p.id = o.product_id "
                "LEFT JOIN warehouses src ON src.id = o.source_warehouse_id "
                "LEFT JOIN warehouses dst ON dst.id = o.target_warehouse_id "
                "WHERE o.status = 'pending' ORDER BY o.id DESC"
            ).fetchall(),
        }
