"""Create local databases if absent, then apply schema and demo data."""

import os

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from checkstock.db import migrate
from checkstock.seed import seed


def create_database(url: str):
    name = conninfo_to_dict(url).get("dbname")
    if not name:
        raise RuntimeError("Database name is missing in URL")
    with psycopg.connect(make_conninfo(url, dbname="postgres"), autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone()
        if not exists:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
            print(f"Created database {name}")


def bootstrap():
    main_url = os.environ.get("DATABASE_URL")
    test_url = os.environ.get("TEST_DATABASE_URL")
    if not main_url or not test_url:
        raise RuntimeError("Set DATABASE_URL and TEST_DATABASE_URL in .env")
    if "YOUR_PASSWORD" in main_url or "YOUR_PASSWORD" in test_url:
        raise RuntimeError("Replace YOUR_PASSWORD in .env")
    main_name = conninfo_to_dict(main_url).get("dbname")
    test_name = conninfo_to_dict(test_url).get("dbname")
    if not main_name or not test_name or main_name == test_name or not test_name.endswith("_test"):
        raise RuntimeError("Use separate main and *_test databases")
    create_database(main_url)
    create_database(test_url)
    migrate()
    seed()
    print("CheckStock database and demo data ready")


if __name__ == "__main__":
    bootstrap()
