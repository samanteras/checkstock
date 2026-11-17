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
                expect(page.locator("#warehouse-message")).to_have_text("Склад добавлен")
                page.locator('#operation-form select[name="kind"]').select_option("fbs")
                page.locator('#product-select').select_option(label="SKU-103 — Корзина")
                page.locator('#source-select').select_option(label=warehouse)
                page.locator('#operation-form input[name="quantity"]').fill("1")
                page.locator('#operation-form button[type="submit"]').click()
                expect(page.locator("#operation-message")).to_contain_text("доступно 0")
                expect(page.locator("#operation-message")).to_have_class("form-message error")
                expect(page.locator("#message")).to_be_empty()
                page.locator('#operation-form select[name="kind"]').select_option("receipt")
                expect(page.locator("#target-field")).to_be_hidden()
                expect(page.locator("#operation-message")).to_be_empty()
                expect(page.locator("#operation-submit")).to_have_text("Оформить приход")
                page.locator('#product-select').select_option(label="SKU-103 — Корзина")
                page.locator('#source-select').select_option(label=warehouse)
                page.locator('#operation-form input[name="quantity"]').fill("2")
                page.locator('#operation-form button[type="submit"]').click()
                expect(page.locator("#operation-message")).to_contain_text("Приход #")
                row = page.locator("#balances tr").filter(has_text="SKU-103").filter(has_text=warehouse)
                expect(row.locator("td").nth(5)).to_have_text("2")
                expect(page.locator("#history")).to_contain_text("Приход")
                page.locator('#operation-form select[name="kind"]').select_option("fbs")
                expect(page.locator("#source-label")).to_have_text("Склад резерва")
                page.locator('#operation-form input[name="quantity"]').fill("1")
                page.locator('#operation-submit').click()
                expect(page.locator("#operation-message")).to_contain_text("Резерв FBS #")
                page.get_by_role("link", name="Перейти к подтверждению").click()
                pending = page.locator("#pending .item").filter(has_text=warehouse)
                expect(pending).to_be_in_viewport()
                pending.get_by_role("button", name="Отменить резерв").click()
                expect(page.locator("#pending-message")).to_contain_text("отменён")
                expect(page.locator("#operation-message")).to_be_empty()
                expect(row.locator("td").nth(5)).to_have_text("2")
                page.locator('#operation-form input[name="quantity"]').fill("3")
                expect(page.locator("#pending-message")).to_be_empty()
                warehouse_row = page.locator("#warehouses .item").filter(has_text=warehouse)
                archive_dialog = page.get_by_role("dialog", name="Архивировать склад?")
                warehouse_row.get_by_role("button", name="Архивировать", exact=True).click()
                expect(archive_dialog).to_be_visible()
                expect(archive_dialog.locator("#archive-name")).to_have_text(warehouse)
                expect(archive_dialog.get_by_role("button", name="Отмена", exact=True)).to_be_focused()
                archive_dialog.get_by_role("button", name="Отмена", exact=True).click()
                expect(archive_dialog).to_be_hidden()
                expect(warehouse_row.get_by_role("button", name="Архивировать", exact=True)).to_be_focused()
                warehouse_row.get_by_role("button", name="Архивировать", exact=True).click()
                page.keyboard.press("Escape")
                expect(archive_dialog).to_be_hidden()
                page.locator("#refresh").click()
                expect(page.locator("#source-select option").filter(has_text=warehouse)).to_have_count(1)
                expect(warehouse_row.locator(".badge")).to_have_count(0)
                warehouse_row.get_by_role("button", name="Архивировать", exact=True).click()
                archive_dialog.get_by_role("button", name="Архивировать", exact=True).click()
                expect(page.locator("#warehouse-message")).to_have_text("Склад архивирован")
                expect(warehouse_row.locator(".badge")).to_have_text("архив")
                expect(page.locator("#source-select option").filter(has_text=warehouse)).to_have_count(0)
                warehouse_row.get_by_role("button", name="Восстановить", exact=True).click()
                expect(page.locator("#warehouse-message")).to_contain_text("Склад восстановлен")
                expect(warehouse_row.locator(".badge")).to_have_count(0)
                expect(warehouse_row.get_by_role("button", name="Архивировать", exact=True)).to_be_visible()
                page.locator("#source-select").select_option(label=warehouse)
                expect(row.locator("td").nth(5)).to_have_text("2")
            finally:
                browser.close()
