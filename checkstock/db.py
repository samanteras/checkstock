"""Database connections and explicit SQL migrations."""

import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row


load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def connect():
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is required; see README.md")
    return psycopg.connect(url, row_factory=dict_row)


def migrate():
    path = Path(__file__).resolve().parent.parent / "migrations" / "001_initial.sql"
    with connect() as conn:
        for statement in path.read_text(encoding="utf-8").split(";"):
            if statement.strip():
                conn.execute(statement)


if __name__ == "__main__":
    migrate()
