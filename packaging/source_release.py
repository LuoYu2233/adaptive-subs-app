"""Build a reproducible, committed-source test package; never includes local runtime data.

Run after committing and verifying changes: python packaging/source_release.py
This is a source distribution, not a Windows executable or a public release.
"""
from __future__ import annotations

import ast
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = {"README.md", "LICENSE", ".env.example", ".gitignore", ".gitattributes", "start.bat"}
FOLDERS = {"app", "extension", "shared", "packaging", "tools", "tests", "docs", ".github"}
MANIFEST = "RELEASE-MANIFEST.json"


def digest(value):
    return hashlib.sha256(value).hexdigest()


def selected(name):
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("Unsafe archive path")
    if name != ".env.example" and (path.name.endswith(".env") or path.name in {".env", "profile.json", "tasks.sqlite3", ".instance.lock", ".instance.json"}
                                  or any(p in {".cache", "logs", ".venv", "__pycache__", "models", "recovery"} for p in path.parts)):
        raise ValueError(f"Runtime or private file must not be tracked for release: {name}")
    return name in ROOT_FILES or (len(path.parts) == 1 and name.startswith("requirements") and name.endswith(".txt")) or path.parts[0] in FOLDERS


def build(root=ROOT):
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
    extension = json.loads(entries["extension/manifest.json"])
    if extension["version"] != constants["EXTENSION_VERSION"] or extension.get("version_name") != version:
        raise ValueError("Server and extension versions do not match")
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
    body = content.getvalue()
    checksum = digest(body)
    if destination.exists() and digest(destination.read_bytes()) != checksum:
        raise ValueError("A different package already exists for this version; increment the version before packaging")
    destination.write_bytes(body)
    destination.with_suffix(".zip.sha256").write_text(f"{checksum}  {destination.name}\n", encoding="utf-8")
    return destination, checksum, revision


if __name__ == "__main__":
    try:
        path, checksum, revision = build()
        print(f"Source test package: {path}\nSHA-256: {checksum}\nCommit: {revision}")
    except (ValueError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
