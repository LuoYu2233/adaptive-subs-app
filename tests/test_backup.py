import copy
import json

import pytest

import profile_store as PS


def sample():
    return {"format": "adaptive-subs-profile", "version": 1, "profile": {
        "identity": "undergrad", "field": "计算机", "exams": {"cet4": 560}, "level": 4500,
        "known": ["sense:bank"], "unknown": ["river"],
        "test": {"est": 4600, "date": "2026-11-24", "known": ["dock"], "unknown": ["margin"], "bands": [1, 0.5, 0]}}}


def test_backup_roundtrip_keeps_exact_level_date_and_marks(tmp_path):
    a, b = PS.Store(tmp_path / "a.json"), PS.Store(tmp_path / "b.json")
    a.import_backup(sample(), 0)
    backup = a.export_backup()
    b.import_backup(backup, 0)
    assert b.export_backup()["profile"] == sample()["profile"]
    # Do not accept credentials or implementation metadata from the stored profile.
    dirty = a.load() | {"api_key": "test-secret", "path": "/tmp/test"}
    a.path.write_text(json.dumps(dirty), encoding="utf-8")
    assert "test-secret" not in json.dumps(a.export_backup())


@pytest.mark.parametrize("change", [
    {"identity": []}, {"exams": {"cet4": True}}, {"level": float("nan")},
    {"unknown": ["sense:bank"]}, {"known": ["river", "river"]},
    {"field": "x" * 41}, {"known": ["../file"]}, {"api_key": "bad"},
    {"test": {"est": 3000, "date": "not-a-date"}},
    {"test": {"est": 3000, "date": "2026-11-24", "bands": [float("inf")]}},
])
def test_invalid_backup_cannot_modify_existing_profile(tmp_path, change):
    store = PS.Store(tmp_path / "profile.json")
    store.import_backup(sample(), 0)
    before = store.path.read_bytes()
    bad = sample()
    bad["profile"].update(change)
    with pytest.raises(ValueError):
        store.import_backup(bad, 1)
    assert store.path.read_bytes() == before


def test_stale_restore_or_clear_cannot_erase_new_feedback(tmp_path):
    store = PS.Store(tmp_path / "profile.json")
    store.import_backup(sample(), 0)
    store.mark("later", "known")
    before = store.path.read_bytes()
    for op in (lambda: store.import_backup(sample(), 1), lambda: store.clear_profile(1), lambda: store.clear_profile(True)):
        with pytest.raises(PS.ProfileConflict):
            op()
        assert store.path.read_bytes() == before
    store.clear_profile(2)
    assert store.export_backup()["profile"] == {}
    assert store.load()["rev"] == 3
    store.import_backup(sample(), 3)
    assert store.load()["rev"] == 4


def test_failed_restore_leaves_profile_and_revision_intact(tmp_path, monkeypatch):
    store = PS.Store(tmp_path / "profile.json")
    store.import_backup(sample(), 0)
    before = store.path.read_bytes()
    def fail(*_):
        raise OSError("disk full")
    monkeypatch.setattr(PS, "write_json", fail)
    with pytest.raises(OSError):
        store.clear_profile(1)
    assert store.path.read_bytes() == before


def test_validation_does_not_modify_input():
    backup = sample()
    prior = copy.deepcopy(backup)
    result = PS.validate_backup(backup)
    result["known"].append("added")
    assert backup == prior
