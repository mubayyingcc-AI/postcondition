"""
Receipt + webhook-event storage.

Two implementations behind one small interface:

  SQLiteStore    - stdlib only; used for local runs and the test suite.
  PostgresStore  - used on Railway (DATABASE_URL=postgres://...).

HONESTY NOTE: SQLiteStore is exercised by tests/test_service.py.
PostgresStore's first real run (Railway, 2026-09-29) crash-looped on a
genuine concurrency bug between gunicorn workers racing on schema
setup — see the advisory-lock comment in PostgresStore.__init__ for
what broke and the fix. That is exactly the kind of thing a live
deploy finds that a sandbox without Postgres cannot.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any, Optional


class SQLiteStore:
    def __init__(self, path: str = "postcondition.db"):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS receipts (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    receipt_id TEXT UNIQUE NOT NULL,
                    reference TEXT NOT NULL,
                    status TEXT NOT NULL,
                    supersedes TEXT,
                    receipt_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS receipts_ref ON receipts(reference);
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    signature_valid INTEGER NOT NULL,
                    body TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            self._conn.commit()

    def save_receipt(self, receipt: dict[str, Any], reference: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO receipts (receipt_id, reference, status, supersedes, receipt_json) VALUES (?,?,?,?,?)",
                (receipt["receipt_id"], reference, receipt["status"], receipt.get("supersedes"), json.dumps(receipt)),
            )
            self._conn.commit()

    def get_receipt(self, receipt_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT receipt_json, reference FROM receipts WHERE receipt_id=?", (receipt_id,)
            ).fetchone()
        return {"receipt": json.loads(row[0]), "reference": row[1]} if row else None

    def latest_for_reference(self, reference: str) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT receipt_json FROM receipts WHERE reference=? ORDER BY seq DESC LIMIT 1", (reference,)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def list_receipts(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT receipt_json FROM receipts ORDER BY seq DESC LIMIT ?", (limit,)
            ).fetchall()
        return [json.loads(r[0]) for r in rows]

    def save_event(self, body: str, signature_valid: bool) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO events (signature_valid, body) VALUES (?,?)", (1 if signature_valid else 0, body)
            )
            self._conn.commit()


class PostgresStore:
    """See the honesty note at the top of this file: not run in the sandbox it was written in."""

    def __init__(self, url: str):
        import psycopg  # imported lazily so SQLite-only setups don't need it

        self._psycopg = psycopg
        self._url = url
        # Multiple gunicorn workers construct a PostgresStore concurrently on
        # boot. Postgres's CREATE ... IF NOT EXISTS is not safe against a
        # genuine race between two sessions — both can pass the "not exists"
        # check before either commits, and the loser gets a UniqueViolation on
        # the catalog insert. Confirmed live on first deploy (2 workers, one
        # crashed with `duplicate key value violates unique constraint
        # "pg_class_relname_nsp_index"`). An advisory lock serializes schema
        # setup: everyone but the first worker just waits, then finds the
        # schema already there and does nothing.
        LOCK_KEY = 8892310147  # arbitrary constant, unique to this app's schema-init lock
        with self._connect() as conn:
            conn.execute("SELECT pg_advisory_lock(%s)", (LOCK_KEY,))
            try:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS receipts (
                        seq BIGSERIAL PRIMARY KEY,
                        receipt_id TEXT UNIQUE NOT NULL,
                        reference TEXT NOT NULL,
                        status TEXT NOT NULL,
                        supersedes TEXT,
                        receipt_json JSONB NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                    """
                )
                conn.execute("CREATE INDEX IF NOT EXISTS receipts_ref ON receipts(reference)")
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS events (
                        seq BIGSERIAL PRIMARY KEY,
                        signature_valid BOOLEAN NOT NULL,
                        body TEXT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                    """
                )
            finally:
                conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))

    def _connect(self):
        return self._psycopg.connect(self._url, autocommit=True)

    def save_receipt(self, receipt: dict[str, Any], reference: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO receipts (receipt_id, reference, status, supersedes, receipt_json) VALUES (%s,%s,%s,%s,%s)",
                (receipt["receipt_id"], reference, receipt["status"], receipt.get("supersedes"), json.dumps(receipt)),
            )

    def get_receipt(self, receipt_id: str) -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT receipt_json::text, reference FROM receipts WHERE receipt_id=%s", (receipt_id,)
            ).fetchone()
        return {"receipt": json.loads(row[0]), "reference": row[1]} if row else None

    def latest_for_reference(self, reference: str) -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT receipt_json::text FROM receipts WHERE reference=%s ORDER BY seq DESC LIMIT 1", (reference,)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def list_receipts(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT receipt_json::text FROM receipts ORDER BY seq DESC LIMIT %s", (limit,)
            ).fetchall()
        return [json.loads(r[0]) for r in rows]

    def save_event(self, body: str, signature_valid: bool) -> None:
        with self._connect() as conn:
            conn.execute("INSERT INTO events (signature_valid, body) VALUES (%s,%s)", (signature_valid, body))


def make_store(database_url: Optional[str]):
    if database_url and database_url.startswith(("postgres://", "postgresql://")):
        return PostgresStore(database_url)
    return SQLiteStore(database_url or "postcondition.db")
