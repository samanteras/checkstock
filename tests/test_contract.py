"""Fast checks that do not require a database server."""

import unittest

from fastapi.testclient import TestClient

from checkstock.api import app


class HttpContractTests(unittest.TestCase):
    def test_page_and_assets_are_served(self):
        with TestClient(app) as client:
            page = client.get("/")
            script = client.get("/static/app.js")
            self.assertEqual(page.status_code, 200)
            self.assertIn("Остатки по складам", page.text)
            self.assertEqual(script.status_code, 200)
            self.assertIn("/api/stock", script.text)

    def test_invalid_quantity_is_rejected_before_database_access(self):
        with TestClient(app) as client:
            response = client.post("/api/receipts", json={"product_id": 1, "warehouse_id": 1,
                "quantity": 0, "idempotency_key": "invalid"})
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.json()["detail"], "Проверьте параметры запроса")
