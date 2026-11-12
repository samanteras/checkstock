"""Idempotent demo data for one shop and two warehouses."""

from checkstock.db import connect


def seed():
    with connect() as conn:
        for name in ("Север", "Юг"):
            conn.execute("INSERT INTO warehouses (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (name,))
        for sku, name in (("SKU-101", "Органайзер"), ("SKU-102", "Контейнер"), ("SKU-103", "Корзина")):
            conn.execute("INSERT INTO products (sku, name) VALUES (%s, %s) ON CONFLICT (sku) DO NOTHING", (sku, name))
        for sku, north, south in (("SKU-101", 10, 6), ("SKU-102", 5, 0), ("SKU-103", 0, 4)):
            product = conn.execute("SELECT id FROM products WHERE sku = %s", (sku,)).fetchone()["id"]
            for warehouse_name, quantity in (("Север", north), ("Юг", south)):
                warehouse = conn.execute("SELECT id FROM warehouses WHERE name = %s", (warehouse_name,)).fetchone()["id"]
                conn.execute(
                    "INSERT INTO balances (product_id, warehouse_id, physical) VALUES (%s, %s, %s) "
                    "ON CONFLICT (product_id, warehouse_id) DO NOTHING",
                    (product, warehouse, quantity),
                )


if __name__ == "__main__":
    seed()
    print("Demo data ready")
