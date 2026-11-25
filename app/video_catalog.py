"""Recover the player catalog from completed subtitle files. All writers share the subtitle store lock."""
from __future__ import annotations

from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path
import stat

from result_validation import utf8_json
from storage import atomic_write_bytes, write_json
from subtitle_store import directory_writer, snapshot


def valid_name(filename):
    return (isinstance(filename, str) and filename.endswith(".json")
            and filename not in {".json", "index.json"} and not filename.endswith(".dry-run.json")
            and not any(c in filename for c in ("/", "\\", "\0")))


def _valid_row(row):
    return (isinstance(row, dict) and valid_name(row.get("file"))
            and row.get("id") == row["file"][:-5] and isinstance(row.get("title"), str)
            and utf8_json(row))


def _stored(folder):
    path = folder / "index.json"
    if path.is_symlink():
        raise OSError("视频索引不能链接到其他文件")
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return [], None
    try:
        rows = json.loads(raw.decode("utf-8-sig"))
    except (ValueError, RecursionError):
        return [], raw
    if not isinstance(rows, list):
        return [], raw
    saved = {}
    for row in rows:
        if _valid_row(row):
            saved.setdefault(row["file"], row)
    return list(saved.values()), raw if len(saved) != len(rows) else None


@lru_cache(maxsize=1024)
def _title(path, version):
    # Only the metadata is retained across the desktop's frequent status requests.
    item = snapshot(path, path.stem)
    return None if item is None else item.data["video"]["title"] or path.stem


def _entries(folder, stored, *, writing=False):
    files = {}
    for path in sorted(folder.glob("*.json")):
        if not valid_name(path.name):
            continue
        try:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode):
                continue
            version = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            title = _title(path, version)
        except OSError as error:
            if writing and not isinstance(error, FileNotFoundError):
                raise
            continue
        if title is not None:
            files[path.name] = ({"file": path.name, "id": path.stem, "title": title}, info.st_mtime)
    ordered = []
    for row in stored:
        existing = files.pop(row["file"], None)
        if existing is not None:
            ordered.append((row, existing[1]))
    return ordered + list(files.values())


def entries(folder):
    folder = Path(folder).absolute()
    try:
        stored, _ = _stored(folder)
    except OSError:
        stored = []
    return _entries(folder, stored)


def read(folder):
    return [row for row, _ in entries(folder)]


def update(folder, ident, title, filename):
    row = {"id": ident, "title": title, "file": filename}
    if not _valid_row(row):
        raise ValueError("视频目录条目无效")
    folder = Path(folder).absolute()
    with directory_writer(folder):
        stored, damaged = _stored(folder)
        rows = [item for item, _ in _entries(folder, stored, writing=True)]
        current = next((item for item in rows if item["file"] == filename), None)
        if current is None:
            raise ValueError("只有已完成的字幕可以加入目录")
        if damaged is not None:
            backup = folder / "recovery" / ("index-" + sha256(damaged).hexdigest() + ".corrupt")
            atomic_write_bytes(backup, damaged)
        updated = [item for item in rows if item["file"] != filename]
        updated.append({**current, **row})
        write_json(folder / "index.json", updated)
