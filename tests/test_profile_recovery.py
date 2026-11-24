import hashlib
import json

import pytest

import profile_store as PS


@pytest.mark.parametrize("raw", [b'{"known":[', b"[]", b"\xff\xfe", b'{"known":"bank"}',
                                 b'{"level":NaN}', b'{"rev":"bad"}', b'{"field":"\\ud800"}', b""])
def test_bad_profiles_cannot_be_silently_overwritten(tmp_path, raw):
    path = tmp_path / "profile.json"
    path.write_bytes(raw)
    store = PS.Store(path)
    for operation in (lambda: store.mark("bank", "known"), lambda: store.update({"field": "new"}),
                      lambda: store.clear_profile(0), store.export_backup, store.payload):
        with pytest.raises(PS.ProfileCorrupt) as caught:
            operation()
        assert caught.value.fingerprint == hashlib.sha256(raw).hexdigest()
        assert path.read_bytes() == raw


def test_recovery_preserves_bytes_and_uses_a_new_revision(tmp_path):
    path = tmp_path / "profile.json"
    raw = b'{"known":["old-record",'
    path.write_bytes(raw)
    store = PS.Store(path)
    fingerprint = hashlib.sha256(raw).hexdigest()
    backup = store.recover(fingerprint)
    from pathlib import Path
    assert Path(backup).read_bytes() == raw
    assert store.export_backup()["profile"] == {}
    assert store.load()["rev"] > 1000000
    before = path.read_bytes()
    with pytest.raises(PS.ProfileConflict):
        store.recover(fingerprint)
    assert path.read_bytes() == before


def test_changed_file_and_failed_backup_prevent_rebuilding(tmp_path, monkeypatch):
    path = tmp_path / "profile.json"
    raw = b'{"known":'
    path.write_bytes(raw)
    store = PS.Store(path)
    with pytest.raises(PS.ProfileConflict):
        store.recover("0" * 64)
    def fail(*_):
        raise OSError("disk full")
    monkeypatch.setattr(PS, "atomic_write_bytes", fail)
    with pytest.raises(OSError):
        store.recover(hashlib.sha256(raw).hexdigest())
    assert path.read_bytes() == raw


def test_failed_rebuild_keeps_original_and_recovery_copy(tmp_path, monkeypatch):
    path = tmp_path / "profile.json"
    raw = b'{"known":'
    path.write_bytes(raw)
    def fail(*_):
        raise OSError("disk full")
    monkeypatch.setattr(PS, "write_json", fail)
    with pytest.raises(OSError):
        PS.Store(path).recover(hashlib.sha256(raw).hexdigest())
    assert path.read_bytes() == raw
    assert next((tmp_path / "recovery").iterdir()).read_bytes() == raw


def test_unreadable_profile_is_not_treated_as_new(tmp_path):
    path = tmp_path / "profile.json"
    path.mkdir()
    with pytest.raises(OSError):
        PS.Store(path).load()
