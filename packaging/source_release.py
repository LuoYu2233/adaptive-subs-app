"""Build a reproducible, committed-source test package; never includes local runtime data.

Run after committing and verifying changes: python packaging/source_release.py
This is a source distribution, not a Windows executable or a public release.
"""
from __future__ import annotations

import ast
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tarfile
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = {"README.md", "LICENSE", ".env.example", ".gitignore", ".gitattributes", "start.bat"}
FOLDERS = {"app", "extension", "shared", "packaging", "tools", "tests", "docs", ".github"}
MANIFEST = "RELEASE-MANIFEST.json"


def digest(value):
    return hashlib.sha256(value).hexdigest()


def selected(name):
    path = PurePosixPath(name)
    if not name or path.is_absolute() or ".." in path.parts or str(path) != name or "\\" in name or ":" in name:
        raise ValueError("Unsafe archive path")
    low = path.name.casefold()
    if name != ".env.example" and (low.endswith(".env") or low.startswith("tasks.sqlite3")
                                  or low in {".env", "profile.json", ".instance.lock", ".instance.json"}
                                  or any(p.casefold() in {".cache", "logs", ".venv", "__pycache__", "models", "recovery"} for p in path.parts)):
        raise ValueError(f"Runtime or private file must not be tracked for release: {name}")
    return name in ROOT_FILES or (len(path.parts) == 1 and name.startswith("requirements") and name.endswith(".txt")) or path.parts[0] in FOLDERS


def snapshot(root=ROOT):
    """Freeze allowlisted committed source; ignored working files are never inputs."""
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=root)

    if git("status", "--porcelain").strip():
        raise ValueError("Commit or resolve working-tree changes before packaging a verified revision")
    revision = git("rev-parse", "HEAD").decode().strip()
    entries = {}
    with tarfile.open(fileobj=io.BytesIO(git("archive", "--format=tar", revision))) as archive:
        for member in archive.getmembers():
            if member.isdir():
                continue
            if not selected(member.name):
                continue
            if not member.isfile():
                raise ValueError(f"Source package does not accept symbolic links: {member.name}")
            entries[member.name] = archive.extractfile(member).read()
    constants = {n.targets[0].id: ast.literal_eval(n.value) for n in ast.parse(entries["app/version.py"].decode()).body if isinstance(n, ast.Assign)}
    version = constants["VERSION"]
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+(?:-[a-z]+\.\d+)?", version):
        raise ValueError("Invalid release version")
    extension = json.loads(entries["extension/manifest.json"])
    if extension["version"] != constants["EXTENSION_VERSION"] or extension.get("version_name") != version:
        raise ValueError("Server and extension versions do not match")
    return entries, version, revision


def write_snapshot(entries, destination):
    """Write selected regular files into a new private staging directory."""
    destination.mkdir(parents=True, exist_ok=False)
    for name, body in entries.items():
        if not selected(name) or "\\" in name or ":" in name:
            raise ValueError(f"Unsafe source path: {name}")
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)


def file_digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def publish(staged, destination):
    """Publish a completed archive atomically, without replacing an existing version.

    Staging must be on the release filesystem. Hard links provide create-if-absent
    semantics on Windows NTFS and POSIX; an unsupported filesystem fails closed.
    """
    checksum = file_digest(staged)
    try:
        os.link(staged, destination)
    except FileExistsError:
        if file_digest(destination) != checksum:
            raise ValueError("A different package already exists for this version; increment the version before packaging")
    fd, name = tempfile.mkstemp(dir=destination.parent, prefix=".checksum-")
    checksum_path = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(f"{checksum}  {destination.name}\n".encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(checksum_path, destination.with_suffix(".zip.sha256"))
    finally:
        checksum_path.unlink(missing_ok=True)
    return checksum


def build(root=ROOT):
    entries, version, revision = snapshot(root)
    manifest = {"format": "adaptive-subs-source-release", "version": version, "commit": revision,
                "files": {name: {"sha256": digest(body), "bytes": len(body)} for name, body in sorted(entries.items())}}
    entries[MANIFEST] = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode()
    destination = root / "release" / f"AdaptiveSubs-{version}-source.zip"
    destination.parent.mkdir(exist_ok=True)
    prefix = f"AdaptiveSubs-{version}-source/"
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, body in sorted(entries.items()):
            info = zipfile.ZipInfo(prefix + name, (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, body)
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix=".source-release-") as temp:
        staged = Path(temp) / "package.zip"
        staged.write_bytes(content.getvalue())
        checksum = publish(staged, destination)
    return destination, checksum, revision


if __name__ == "__main__":
    try:
        path, checksum, revision = build()
        print(f"Source test package: {path}\nSHA-256: {checksum}\nCommit: {revision}")
    except (ValueError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
