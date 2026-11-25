"""Durable local task snapshots. No API keys or browser cookies are stored here."""
from __future__ import annotations

import json
import hashlib
import math
import os
from pathlib import Path
import sqlite3
import threading
import time
import tempfile
import uuid

from storage import atomic_copy


class TaskStoreCorrupt(ValueError):
    def __init__(self, fingerprint):
        super().__init__("任务记录损坏或格式不兼容，请在控制台查看恢复选项")
        self.fingerprint = fingerprint


class TaskStoreConflict(ValueError):
    pass


class TaskStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self._json_functions = True

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        existing = self.path.exists()
        if existing and self.path.stat().st_size == 0:
            raise self._corrupt()
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            # Keep all durable state in one database; use a local disk for installations.
            if existing:
                columns = [row[1] for row in connection.execute("PRAGMA table_info(jobs)")]
                if columns != ["id", "state", "updated", "payload"]:
                    raise self._corrupt()
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("""CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, state TEXT NOT NULL, updated REAL NOT NULL, payload TEXT NOT NULL
            )""")
        except Exception as error:
            connection.close()
            self._raise_corrupt(error)
            raise
        return connection

    def fingerprint(self):
        # A pending journal/WAL needs SQLite or offline recovery, not a file replacement.
        if any(Path(str(self.path) + suffix).exists() for suffix in ("-journal", "-wal", "-shm")):
            return None
        with self.path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()

    def _corrupt(self):
        return TaskStoreCorrupt(self.fingerprint())

    def _raise_corrupt(self, error):
        if isinstance(error, sqlite3.Error) and getattr(error, "sqlite_errorcode", 0) & 255 in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB):
            raise self._corrupt() from error

    def _decode(self, state, updated, raw):
        try:
            payload = json.loads(raw, parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if state not in ("running", "cancelling", "cancelled", "error", "partial", "interrupted", "done"):
                raise ValueError
            if not isinstance(payload, dict) or type(updated) not in (int, float) or not math.isfinite(updated):
                raise ValueError
            for key in ("started", "duration"):
                value = payload.get(key, 0)
                if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                    raise ValueError
            for key in ("done", "total"):
                if type(payload.get(key, 0)) is not int or payload.get(key, 0) < 0:
                    raise ValueError
            if not isinstance(payload.get("title", ""), str):
                raise ValueError
            for key in ("ann", "zh", "usage", "asr_usage", "asr_stats"):
                if not isinstance(payload.get(key, {}), dict):
                    raise ValueError
            for key in ("cues", "failed_tasks"):
                if not isinstance(payload.get(key, []), list):
                    raise ValueError
        except (ValueError, TypeError, OverflowError, RecursionError):
            raise self._corrupt() from None
        return {"state": state, "updated": updated, "payload": payload}

    def save(self, vid: str, state: str, payload: dict):
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        with self._lock:
            connection = self._connect()
            try:
                old = connection.execute("SELECT state, updated, payload FROM jobs WHERE id = ?", (vid,)).fetchone()
                if old is not None:
                    self._decode(*old)  # Never overwrite an unreadable saved task with a new snapshot.
                with connection:
                    connection.execute("INSERT OR REPLACE INTO jobs VALUES (?, ?, ?, ?)",
                                       (vid, state, time.time(), encoded))
            except sqlite3.Error as error:
                self._raise_corrupt(error)
                raise
            finally:
                connection.close()

    def load(self, vid: str) -> dict | None:
        with self._lock:
            connection = self._connect()
            try:
                row = connection.execute("SELECT state, updated, payload FROM jobs WHERE id = ?", (vid,)).fetchone()
                return None if row is None else self._decode(*row)
            except sqlite3.Error as error:
                self._raise_corrupt(error)
                raise
            finally:
                connection.close()

    def recent(self, limit=20, *, summary=False) -> list[dict]:
        with self._lock:
            connection = self._connect()
            try:
                expression = "payload"
                if summary and self._json_functions:
                    # Validate the same top-level types as a full read before removing bulky fields.
                    # Corrupt records fall through unchanged so _decode still reports the problem.
                    fields = {"cues": "array", "ann": "object", "zh": "object", "usage": "object",
                              "asr_usage": "object", "failed_tasks": "array"}
                    invalid = " OR ".join(f"COALESCE(json_type(payload, '$.{key}'), '{kind}') != '{kind}'" for key, kind in fields.items())
                    remove = ", ".join(f"'$.{key}'" for key in (*fields, "sub"))
                    expression = f"CASE WHEN NOT json_valid(payload) THEN payload WHEN {invalid} THEN payload ELSE json_remove(payload, {remove}) END"
                try:
                    rows = connection.execute(f"SELECT id, state, updated, {expression} FROM jobs ORDER BY updated DESC LIMIT ?",
                                              (min(100, max(1, limit)),)).fetchall()
                except sqlite3.OperationalError as error:
                    if expression == "payload" or "no such function: json_" not in str(error):
                        raise
                    self._json_functions = False  # Custom SQLite builds may omit JSON support.
                    rows = connection.execute("SELECT id, state, updated, payload FROM jobs ORDER BY updated DESC LIMIT ?",
                                              (min(100, max(1, limit)),)).fetchall()
                return [{"id": row[0], **self._decode(*row[1:])} for row in rows]
            except sqlite3.Error as error:
                self._raise_corrupt(error)
                raise
            finally:
                connection.close()

    def mark_interrupted(self):
        with self._lock:
            connection = self._connect()
            try:
                with connection:
                    connection.execute("UPDATE jobs SET state='interrupted' WHERE state IN ('running', 'cancelling')")
            except sqlite3.Error as error:
                self._raise_corrupt(error)
                raise
            finally:
                connection.close()

    def recover(self, fingerprint):
        with self._lock:
            if not fingerprint or self.fingerprint() != fingerprint:
                raise TaskStoreConflict("任务文件已变化或有未完成事务，请刷新控制台后重试")
            try:
                self.recent()
            except TaskStoreCorrupt:
                pass
            else:
                raise TaskStoreConflict("任务记录当前可读，无需重建")
            backup = self.path.parent / "recovery" / f"tasks-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}.sqlite3.corrupt"
            atomic_copy(self.path, backup)
            with backup.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != fingerprint:
                    raise TaskStoreConflict("备份期间任务文件变化，原任务库未替换")
            with tempfile.TemporaryDirectory(prefix=".asubs-tasks-", dir=self.path.parent) as temp:
                replacement = Path(temp) / "tasks.sqlite3"
                TaskStore(replacement).recent()
                if self.fingerprint() != fingerprint:
                    raise TaskStoreConflict("任务文件已变化，原任务库未替换")
                os.replace(replacement, self.path)
            return str(backup)
