"""Verify source package content hashes. Integrity checking is not publisher authentication."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
import zipfile


def verify(path):
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        names = [m.filename for m in members]
        if len(names) != len(set(names)) or len(names) > 10000 or sum(m.file_size for m in members) > 500_000_000:
            raise ValueError("Duplicate files or package too large")
        for name in names:
            p = PurePosixPath(name)
            if p.is_absolute() or ".." in p.parts or "\\" in name:
                raise ValueError("Unsafe package path")
        manifests = [n for n in names if n.endswith("/RELEASE-MANIFEST.json")]
        if len(manifests) != 1:
            raise ValueError("Missing or duplicate release manifest")
        prefix = manifests[0].removesuffix("RELEASE-MANIFEST.json")
        manifest = json.loads(archive.read(manifests[0]))
        if manifest.get("format") != "adaptive-subs-source-release" or not isinstance(manifest.get("files"), dict):
            raise ValueError("Unsupported release manifest")
        expected = {prefix + n for n in manifest["files"]} | {manifests[0]}
        if set(names) != expected:
            raise ValueError("Package contains missing or unexpected files")
        for name, entry in manifest["files"].items():
            value = archive.read(prefix + name)
            if len(value) != entry["bytes"] or hashlib.sha256(value).hexdigest() != entry["sha256"]:
                raise ValueError(f"Content verification failed: {name}")
        return manifest


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: python packaging/verify_source_release.py PACKAGE.zip")
    try:
        manifest = verify(Path(sys.argv[1]))
        print(f"Verified {len(manifest['files'])} files; version {manifest['version']}; commit {manifest['commit']}")
    except (ValueError, KeyError, OSError, zipfile.BadZipFile) as error:
        sys.exit(f"Verification failed: {error}")
