"""Durable local task snapshots. No API keys or browser cookies are stored here."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import threading
import time


class TaskStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        # Keep all durable state in one database; use a local disk for installations.
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("""CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, state TEXT NOT NULL, updated REAL NOT NULL, payload TEXT NOT NULL
        )""")
        return connection

    def save(self, vid: str, state: str, payload: dict):
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        with self._lock:
            connection = self._connect()
            try:
                with connection:
                    connection.execute("INSERT OR REPLACE INTO jobs VALUES (?, ?, ?, ?)",
                                       (vid, state, time.time(), encoded))
            finally:
                connection.close()

    def load(self, vid: str) -> dict | None:
        with self._lock:
            connection = self._connect()
            try:
                row = connection.execute("SELECT state, updated, payload FROM jobs WHERE id = ?", (vid,)).fetchone()
            finally:
                connection.close()
        if row is None:
            return None
        return {"state": row[0], "updated": row[1], "payload": json.loads(row[2])}

    def recent(self, limit=20) -> list[dict]:
        with self._lock:
            connection = self._connect()
            try:
                rows = connection.execute("SELECT id, state, updated, payload FROM jobs ORDER BY updated DESC LIMIT ?",
                                          (min(100, max(1, limit)),)).fetchall()
            finally:
                connection.close()
        return [{"id": r[0], "state": r[1], "updated": r[2], "payload": json.loads(r[3])} for r in rows]

    def mark_interrupted(self):
        with self._lock:
            connection = self._connect()
            try:
                with connection:
                    connection.execute("UPDATE jobs SET state='interrupted' WHERE state IN ('running', 'cancelling')")
            finally:
                connection.close()

