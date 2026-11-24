import importlib.util
import json
from pathlib import Path
import subprocess
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "packaging" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BUILD = load_module("source_release")
VERIFY = load_module("verify_source_release")


def test_reproducible_committed_source_excludes_local_data_and_detects_tampering(tmp_path):
    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)
    git("init")
    (tmp_path / "app").mkdir()
    (tmp_path / "extension").mkdir()
    (tmp_path / "app/version.py").write_text('VERSION = "0.4.0-alpha.1"\nEXTENSION_VERSION = "0.4.0.1"\n')
    (tmp_path / "extension/manifest.json").write_text(json.dumps({"version": "0.4.0.1", "version_name": "0.4.0-alpha.1"}))
    (tmp_path / "README.md").write_text("test source")
    (tmp_path / ".gitignore").write_text(".env\nprofile.json\nrelease/\n")
    git("add", ".")
    git("-c", "user.name=Release Test", "-c", "user.email=test@example.invalid", "commit", "-m", "fixture")
    (tmp_path / ".env").write_text("MIMO_API_KEY=test-secret-never-package")
    (tmp_path / "profile.json").write_text('{"field":"private-learning-record"}')
    path, checksum, commit = BUILD.build(tmp_path)
    before = path.read_bytes()
    assert BUILD.build(tmp_path)[1] == checksum and path.read_bytes() == before
    manifest = VERIFY.verify(path)
    assert manifest["commit"] == commit
    assert ".env" not in manifest["files"] and "profile.json" not in manifest["files"]
    with zipfile.ZipFile(path) as archive:
        bodies = {n: archive.read(n) for n in archive.namelist()}
    assert b"test-secret-never-package" not in b"".join(bodies.values())
    bodies[next(n for n in bodies if n.endswith("/README.md"))] = b"modified"
    altered = tmp_path / "release/altered.zip"
    with zipfile.ZipFile(altered, "w") as archive:
        for name, body in bodies.items():
            archive.writestr(name, body)
    with pytest.raises(ValueError, match="Content verification failed"):
        VERIFY.verify(altered)
    (tmp_path / "README.md").write_text("uncommitted change")
    with pytest.raises(ValueError, match="working-tree"):
        BUILD.build(tmp_path)


@pytest.mark.parametrize("name", [".env", "app/config.env", "app/profile.json", "app/logs/output.txt", "app/.cache/output.json"])
def test_release_rejects_accidentally_tracked_private_data(name):
    with pytest.raises(ValueError):
        BUILD.selected(name)


def test_real_extension_version_matches_program():
    from version import VERSION, EXTENSION_VERSION
    manifest = json.loads((ROOT / "extension/manifest.json").read_text())
    assert manifest["version"] == EXTENSION_VERSION
    assert manifest["version_name"] == VERSION
