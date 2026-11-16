"""One real-browser smoke path; run against a seeded local server."""

import os
import unittest
from uuid import uuid4


class BrowserFlowTests(unittest.TestCase):
    def test_add_warehouse_and_receive_stock(self):
        base_url = os.environ.get("CHECKSTOCK_BASE_URL")
        if not base_url:
            self.skipTest("Set CHECKSTOCK_BASE_URL to a running seeded CheckStock app")
        try:
            from playwright.sync_api import expect, sync_playwright
        except ImportError:
            self.skipTest("Install requirements-browser.txt and run playwright install chromium")

        warehouse = "Тест-" + uuid4().hex[:8]
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(base_url, wait_until="networkidle")
                page.locator('#warehouse-form input[name="name"]').fill(warehouse)
                page.locator('#warehouse-form button[type="submit"]').click()
                page.locator("#warehouses").get_by_text(warehouse, exact=True).wait_for()
                page.locator('#operation-form select[name="kind"]').select_option("receipt")
                page.locator('#product-select').select_option(label="SKU-103 — Корзина")
                page.locator('#source-select').select_option(label=warehouse)
                page.locator('#operation-form input[name="quantity"]').fill("2")
                page.locator('#operation-form button[type="submit"]').click()
                page.get_by_text("Операция #", exact=False).wait_for()
                row = page.locator("#balances tr").filter(has_text="SKU-103").filter(has_text=warehouse)
                expect(row.locator("td").nth(5)).to_have_text("2")
                expect(page.locator("#history")).to_contain_text("Приход")
            finally:
                browser.close()
