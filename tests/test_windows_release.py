"""Portable release checks use synthetic bytes, never pretend to build/run Windows EXEs."""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packaging"))
import source_release as source
from verify_source_release import verify

spec = importlib.util.spec_from_file_location("windows_build", ROOT / "packaging/build.py")
windows = importlib.util.module_from_spec(spec)
spec.loader.exec_module(windows)


@pytest.fixture
def native_output(tmp_path):
    program = tmp_path / "fresh-output"
    (program / "_internal" / "models").mkdir(parents=True)
    (program / "AdaptiveSubs.exe").write_bytes(b"synthetic EXE placeholder, not executable")
    (program / "_internal" / "models" / "model.bin").write_bytes(b"synthetic model")
    extension = {"extension/manifest.json": b'{"version":"0.4.0.99","version_name":"0.4.0-alpha.99"}',
                 "extension/content.js": b"committed extension", "README.md": b"source only"}
    return program, extension


def build_fixture(tmp_path, native_output):
    return windows.package(*native_output, "0.4.0-alpha.99", "a" * 40,
                           {"validation": "synthetic packaging test only"}, release=tmp_path / "release")


def test_windows_archive_verifies_versions_hashes_and_preserves_previous_version(tmp_path, native_output):
    path, checksum = build_fixture(tmp_path, native_output)
    before = path.read_bytes()
    manifest = verify(path, kind="windows")
    assert manifest["version"] == "0.4.0-alpha.99" and manifest["commit"] == "a" * 40
    assert "程序/_internal/models/model.bin" in manifest["files"]
    assert "浏览器插件/content.js" in manifest["files"]
    assert "README.md" not in manifest["files"]
    assert path.with_suffix(".zip.sha256").read_text(encoding="utf-8") == f"{checksum}  {path.name}\n"
    assert build_fixture(tmp_path, native_output) == (path, checksum)
    (native_output[0] / "AdaptiveSubs.exe").write_bytes(b"different build")
    with pytest.raises(ValueError, match="increment the version"):
        build_fixture(tmp_path, native_output)
    assert path.read_bytes() == before
    assert not list(path.parent.glob(".windows-release-*"))


def test_snapshot_never_collects_ignored_extension_settings_or_old_dist(tmp_path):
    def git(*args):
        return subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)
    git("init")
    (tmp_path / "app").mkdir()
    (tmp_path / "extension").mkdir()
    (tmp_path / "app/version.py").write_text('VERSION="0.4.0-alpha.99"\nEXTENSION_VERSION="0.4.0.99"\n', encoding="utf-8")
    (tmp_path / "extension/manifest.json").write_text('{"version":"0.4.0.99","version_name":"0.4.0-alpha.99"}', encoding="utf-8")
    (tmp_path / ".gitignore").write_text("*.env\nprofile.json\nbuild/\n", encoding="utf-8")
    git("add", ".")
    git("-c", "user.name=Release Test", "-c", "user.email=test@example.invalid", "commit", "-m", "fixture")
    (tmp_path / "extension/private.env").write_text("SYNTHETIC_SECRET=not-for-delivery", encoding="utf-8")
    (tmp_path / "extension/profile.json").write_text('{"marks":"synthetic private record"}', encoding="utf-8")
    (tmp_path / "build").mkdir()
    (tmp_path / "build/stale.exe").write_bytes(b"old version")
    entries, _, _ = source.snapshot(tmp_path)
    assert not any("private" in name or "profile.json" in name or name.startswith("build/") for name in entries)
    # Even after a working-tree change, staging remains the exact frozen commit.
    (tmp_path / "extension/manifest.json").write_text("uncommitted mutation", encoding="utf-8")
    source.write_snapshot(entries, tmp_path / "staging")
    assert json.loads((tmp_path / "staging/extension/manifest.json").read_text(encoding="utf-8"))["version"] == "0.4.0.99"
    assert not (tmp_path / "staging/extension/private.env").exists()


@pytest.mark.parametrize("name", ["config.env", "profile.json", "tasks.sqlite3-wal", ".instance.json", "logs/trace.txt"])
def test_native_runtime_files_fail_before_publication(tmp_path, native_output, name):
    path = native_output[0] / "_internal" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"synthetic personal data")
    with pytest.raises(ValueError, match="Runtime or private"):
        build_fixture(tmp_path, native_output)
    assert not list(tmp_path.glob("release/*.zip"))


@pytest.mark.parametrize("name", ["../escape", "a/../../escape", "a\\escape", "a:stream", "CON.txt", "a./file", "a//b", "/absolute"])
def test_windows_paths_cannot_escape_alias_or_use_alternate_streams(name):
    with pytest.raises(ValueError, match="Unsafe package path"):
        windows.safe_name(name)


def test_windows_case_collision_is_rejected(tmp_path, native_output):
    native_output[1]["extension/Content.js"] = b"conflicting content"
    with pytest.raises(ValueError, match="collision"):
        build_fixture(tmp_path, native_output)


def test_archive_read_failure_does_not_publish_partial_zip(tmp_path, native_output, monkeypatch):
    original = Path.open
    def failed(path, *args, **kwargs):
        if path.name == "model.bin":
            raise OSError("simulated disk read failure")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", failed)
    with pytest.raises(OSError, match="disk read failure"):
        build_fixture(tmp_path, native_output)
    assert not list((tmp_path / "release").iterdir())


def test_concurrent_different_publishers_cannot_overwrite_the_winner(tmp_path):
    staged = [tmp_path / "first.tmp", tmp_path / "second.tmp"]
    for i, path in enumerate(staged):
        path.write_bytes(f"complete-content-{i}".encode())
    destination = tmp_path / "version.zip"
    def attempt(path):
        try:
            return source.publish(path, destination)
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, staged))
    winner = next(result for result in outcomes if result)
    assert outcomes.count(None) == 1 and source.file_digest(destination) == winner
    assert destination.with_suffix(".zip.sha256").read_text(encoding="utf-8").startswith(winner)


def test_symlinked_native_content_is_not_read(tmp_path, native_output, create_file_symlink):
    link = native_output[0] / "_internal/private-link"
    secret = tmp_path / "outside.txt"
    secret.write_text("synthetic private bytes", encoding="utf-8")
    create_file_symlink(link, secret)
    with pytest.raises(ValueError, match="Symbolic links"):
        build_fixture(tmp_path, native_output)


def test_native_tampering_and_link_entries_are_rejected(tmp_path, native_output):
    path, _ = build_fixture(tmp_path, native_output)
    altered = tmp_path / "altered.zip"
    with zipfile.ZipFile(path) as archive:
        bodies = {name: archive.read(name) for name in archive.namelist()}
    exe = next(name for name in bodies if name.endswith("/AdaptiveSubs.exe"))
    bodies[exe] += b"tampered"
    with zipfile.ZipFile(altered, "w") as archive:
        for name, body in bodies.items():
            archive.writestr(name, body)
    with pytest.raises(ValueError, match="Content verification failed"):
        verify(altered, kind="windows")
    with zipfile.ZipFile(altered, "w") as archive:
        for name, body in bodies.items():
            info = zipfile.ZipInfo(name)
            info.external_attr = (0o120777 if name == exe else 0o100644) << 16
            archive.writestr(info, body)
    with pytest.raises(ValueError, match="Unsafe package path"):
        verify(altered, kind="windows")
