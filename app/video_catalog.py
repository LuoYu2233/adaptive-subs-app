"""Rebuildable video metadata; completed subtitle files remain the source of truth."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import threading
import time

from result_validation import valid_result
from storage import atomic_copy, write_json

LOCK = threading.Lock()
MAX_INDEX_BYTES = 4 * 1024 * 1024
MAX_RESULT_BYTES = 32 * 1024 * 1024


def valid_entry(row):
    if not isinstance(row, dict):
        return False
    ident, title, filename = row.get("id"), row.get("title"), row.get("file")
    valid = (isinstance(ident, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", ident) and isinstance(title, str) and len(title) <= 2000
            and isinstance(filename, str) and filename == ident + ".json" and filename != "index.json"
            and Path(filename).name == filename and not any(c in filename for c in "\\/\x00"))
    if not valid:
        return False
    try:
        for value in (ident, title, filename):
            value.encode("utf-8")
        return True
    except UnicodeEncodeError:
        return False


def _stored(folder):
    path = folder / "index.json"
    try:
        if path.stat().st_size > MAX_INDEX_BYTES:
            raise ValueError("Video index is too large")
        rows = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(rows, list) or any(not valid_entry(row) for row in rows)
                or len({row["id"] for row in rows}) != len(rows)):
            raise ValueError("Invalid video index")
        return rows, False
    except FileNotFoundError:
        return [], False
    except (ValueError, RecursionError):
        return [], True


def _inside(path, folder):
    try:
        path.resolve().relative_to(folder.resolve())
        return path.is_file()
    except (ValueError, OSError):
        return False


def _collect(folder, stored):
    rows = {row["file"]: {key: row[key] for key in ("id", "title", "file")} for row in stored
            if _inside(folder / row["file"], folder)}
    for path in sorted(folder.glob("*.json")):
        if path.name == "index.json" or path.name in rows or not _inside(path, folder):
            continue
        try:
            if path.stat().st_size > MAX_RESULT_BYTES:
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            if not valid_result(data) or data["video"]["id"] != path.stem:
                continue
            row = {"id": path.stem, "title": (data["video"]["title"] or path.stem)[:2000], "file": path.name}
            if valid_entry(row):
                rows[path.name] = row
        except (OSError, ValueError, RecursionError):
            continue  # Incomplete/invalid results cannot be advertised as ready videos.
    return list(rows.values())


def read(folder: Path):
    """Read only: missing/corrupt/stale metadata never hides a completed video file."""
    try:
        rows, _ = _stored(folder)
    except OSError:
        rows = []  # An unreadable index does not imply that the subtitle files are unreadable.
    return _collect(folder, rows)


def _fingerprint(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.digest()


def update(folder: Path, ident: str, title: str, filename: str):
    entry = {"id": ident, "title": title[:2000] if isinstance(title, str) else title, "file": filename}
    if not valid_entry(entry):
        raise ValueError("视频目录条目无效")
    with LOCK:
        rows, corrupt = _stored(folder)
        rows = _collect(folder, rows)
        if corrupt:
            path = folder / "index.json"
            fingerprint = _fingerprint(path)
            backup = folder / "recovery" / f"index-{time.time_ns()}.json.corrupt"
            atomic_copy(path, backup)
            if _fingerprint(backup) != fingerprint or _fingerprint(path) != fingerprint:
                raise OSError("目录索引在备份时发生变化，未覆盖原文件")
        rows = [row for row in rows if row["id"] != ident] + [entry]
        if len(json.dumps(rows, ensure_ascii=False).encode("utf-8")) > MAX_INDEX_BYTES:
            raise ValueError("视频目录超过可写入大小，字幕文件仍保留")
        write_json(folder / "index.json", rows)
