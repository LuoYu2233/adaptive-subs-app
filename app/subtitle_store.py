"""Publish complete subtitle files atomically. A deferred repair may only replace the version it read."""
from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import NamedTuple

from result_validation import valid_result
from storage import atomic_write_bytes


class Snapshot(NamedTuple):
    data: dict
    digest: bytes


def snapshot(path: Path, expected_id: str) -> Snapshot | None:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (ValueError, RecursionError):
        return None
    if not valid_result(data) or data["video"]["id"] != expected_id:
        return None
    return Snapshot(data, sha256(raw).digest())


@contextmanager
def directory_writer(folder: Path):
    folder.mkdir(parents=True, exist_ok=True)
    # A persistent separate file keeps the lock valid across replacements of results and the catalog.
    with (folder / ".subtitle-store.lock").open("a+b") as handle:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield


def publish(path: Path, data: dict, *, expected_digest: bytes | None = None) -> bool:
    if not valid_result(data) or data["video"]["id"] != path.stem:
        raise ValueError("生成结果的字幕、时间或注释位置无效")
    raw = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    with directory_writer(path.parent):
        if expected_digest is not None:
            try:
                current = path.read_bytes()
            except FileNotFoundError:
                return False
            if sha256(current).digest() != expected_digest:
                return False
        atomic_write_bytes(path, raw)
    return True
