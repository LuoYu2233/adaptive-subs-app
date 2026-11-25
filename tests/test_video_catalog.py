from concurrent.futures import ThreadPoolExecutor
import json
from unittest.mock import Mock

import pytest

import video_catalog as C
import storage


def video(folder, ident="aaaaaaaaaaa", **extra):
    data = {"video": {"id": ident, "title": f"Original {ident}", "duration": 1},
            "cues": [{"s": 0, "e": 1, "text": "Original test sentence.", "notes": []}], **extra}
    (folder / f"{ident}.json").write_text(json.dumps(data), encoding="utf-8")
    return {"id": ident, "title": data["video"]["title"], "file": f"{ident}.json"}


@pytest.mark.parametrize("raw", [b'{broken', b'[]junk', b'{}', b'[{}]', b'[{},"broken"]', b'\xff'])
def test_bad_catalog_reads_completed_files_without_modifying_original(tmp_path, raw):
    first, second = video(tmp_path), video(tmp_path, "bbbbbbbbbbb")
    (tmp_path / "index.json").write_bytes(raw)
    assert C.read(tmp_path) == [first, second]
    assert (tmp_path / "index.json").read_bytes() == raw
    assert not (tmp_path / "recovery").exists()
    C.update(tmp_path, second["id"], second["title"], second["file"])
    assert json.loads((tmp_path / "index.json").read_text(encoding="utf-8")) == [first, second]
    assert next((tmp_path / "recovery").iterdir()).read_bytes() == raw


def test_valid_catalog_reconciles_missing_entries_and_removed_files(tmp_path):
    first, second = video(tmp_path), video(tmp_path, "bbbbbbbbbbb")
    removed = {"id": "ccccccccccc", "title": "Removed", "file": "ccccccccccc.json"}
    raw = json.dumps([first, removed])
    (tmp_path / "index.json").write_text(raw, encoding="utf-8")
    assert C.read(tmp_path) == [first, second]
    assert (tmp_path / "index.json").read_text(encoding="utf-8") == raw


def test_invalid_later_row_duplicates_and_unsafe_paths_are_not_trusted(tmp_path):
    first = video(tmp_path)
    for value in ([first, "broken"], [first, first], [first, {**first, "file": "../outside.json"}],
                  [{**first, "title": "\ud800"}]):
        (tmp_path / "index.json").write_text(json.dumps(value), encoding="utf-8")
        assert C.read(tmp_path) == [first]


def test_scan_ignores_incomplete_results_and_mismatched_ids(tmp_path):
    first = video(tmp_path)
    video(tmp_path, "bbbbbbbbbbb", status="partial")
    video(tmp_path, "unservable video")
    (tmp_path / "broken.json").write_text('{"video":{}}', encoding="utf-8")
    (tmp_path / "wrongname.json").write_bytes((tmp_path / first["file"]).read_bytes())
    assert C.read(tmp_path) == [first]


def test_scan_ignores_external_link(tmp_path, create_file_symlink):
    library = tmp_path / "library"
    library.mkdir()
    row = video(tmp_path)
    create_file_symlink(library / row["file"], tmp_path / row["file"])
    assert C.read(library) == []


@pytest.mark.parametrize("failure", ["backup", "replace"])
def test_catalog_recovery_failure_preserves_raw_index(tmp_path, monkeypatch, failure):
    first = video(tmp_path)
    raw = b"broken original index"
    (tmp_path / "index.json").write_bytes(raw)
    if failure == "backup":
        monkeypatch.setattr(C, "atomic_copy", Mock(side_effect=OSError("backup failed")))
    else:
        monkeypatch.setattr(C, "write_json", Mock(side_effect=OSError("replace failed")))
    with pytest.raises(OSError): C.update(tmp_path, first["id"], first["title"], first["file"])
    assert (tmp_path / "index.json").read_bytes() == raw


def test_parallel_catalog_updates_keep_every_completed_file(tmp_path):
    entries = [video(tmp_path, f"video{i}") for i in range(20)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda row: C.update(tmp_path, row["id"], row["title"], row["file"]), entries))
    assert {row["id"] for row in C.read(tmp_path)} == {row["id"] for row in entries}
    assert not (tmp_path / "recovery").exists()


def test_changed_original_during_backup_is_not_overwritten(tmp_path, monkeypatch):
    first = video(tmp_path)
    path = tmp_path / "index.json"
    path.write_bytes(b"broken original")
    def changed(source, target):
        storage.atomic_copy(source, target)
        source.write_text("changed while backing up", encoding="utf-8")
    monkeypatch.setattr(C, "atomic_copy", changed)
    with pytest.raises(OSError): C.update(tmp_path, first["id"], first["title"], first["file"])
    assert path.read_text(encoding="utf-8") == "changed while backing up"
