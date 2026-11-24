"""Verified model installation cannot activate partial downloads or replace existing data."""
import hashlib
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("prepare_local_model", Path(__file__).resolve().parents[1] / "tools/prepare_local_model.py")
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)


@pytest.fixture
def source(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    body = b"small original test fixture"
    (source / "model.bin").write_bytes(body)
    monkeypatch.setattr(M, "MANIFEST", {"files": {"model.bin": {
        "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}}, "repo": "test", "revision": "test"})
    return source


def test_verified_offline_install_and_existing_good_copy(source, tmp_path):
    destination = tmp_path / "installed"
    assert M.install(destination, source) == destination
    M.verify(destination)
    assert (destination / "MODEL-SOURCE.json").is_file()
    assert M.install(destination, tmp_path / "missing") == destination


def test_broken_source_never_becomes_available(source, tmp_path):
    data = source / "model.bin"
    data.write_bytes(b"x" * data.stat().st_size)
    destination = tmp_path / "installed"
    with pytest.raises(ValueError, match="校验失败"):
        M.install(destination, source)
    assert not destination.exists()
    assert not list(tmp_path.glob(".asubs-model-*"))


def test_existing_different_model_is_preserved(source, tmp_path):
    destination = tmp_path / "installed"
    destination.mkdir()
    original = destination / "model.bin"
    original.write_bytes(b"custom user model")
    with pytest.raises(ValueError):
        M.install(destination, source)
    assert original.read_bytes() == b"custom user model"
