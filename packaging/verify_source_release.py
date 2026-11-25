"""Verify source package content hashes. Integrity checking is not publisher authentication."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import zipfile


def verify(path, kind="source"):
    if kind not in {"source", "windows"}:
        raise ValueError("Unsupported package kind")
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        names = [m.filename for m in members]
        limit = 500_000_000 if kind == "source" else 6_000_000_000
        if len(names) != len({n.casefold() for n in names}) or len(names) > 10000 or sum(m.file_size for m in members) > limit:
            raise ValueError("Duplicate files or package too large")
        for member in members:
            name = member.filename
            p = PurePosixPath(name)
            if (not name or str(p) != name or p.is_absolute() or ".." in p.parts or "\\" in name
                    or ":" in name or any(part.endswith((".", " ")) for part in p.parts)
                    or any(re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])(?:\..*)?", part) for part in p.parts)
                    or re.search(r'[<>"|?*\x00-\x1f]', name)
                    or stat.S_IFMT(member.external_attr >> 16) not in {0, stat.S_IFREG}):
                raise ValueError("Unsafe package path")
        manifests = [n for n in names if n.endswith("/RELEASE-MANIFEST.json")]
        if len(manifests) != 1:
            raise ValueError("Missing or duplicate release manifest")
        prefix = manifests[0].removesuffix("RELEASE-MANIFEST.json")
        if prefix.count("/") != 1 or archive.getinfo(manifests[0]).file_size > 3_000_000:
            raise ValueError("Invalid release manifest location or size")
        manifest = json.loads(archive.read(manifests[0]))
        if (not isinstance(manifest, dict) or manifest.get("format") != f"adaptive-subs-{kind}-release"
                or not isinstance(manifest.get("files"), dict)):
            raise ValueError("Unsupported release manifest")
        expected = {prefix + n for n in manifest["files"]} | {manifests[0]}
        if set(names) != expected:
            raise ValueError("Package contains missing or unexpected files")
        for name, entry in manifest["files"].items():
            if (not isinstance(entry, dict) or type(entry.get("bytes")) is not int
                    or entry["bytes"] < 0 or not isinstance(entry.get("sha256"), str)
                    or not re.fullmatch(r"[a-f0-9]{64}", entry["sha256"])):
                raise ValueError(f"Invalid file manifest entry: {name}")
            with archive.open(prefix + name) as stream:
                checksum = hashlib.file_digest(stream, "sha256").hexdigest()
            if archive.getinfo(prefix + name).file_size != entry["bytes"] or checksum != entry["sha256"]:
                raise ValueError(f"Content verification failed: {name}")
        if kind == "windows" and not {"程序/AdaptiveSubs.exe", "浏览器插件/manifest.json", "使用说明.txt"} <= manifest["files"].keys():
            raise ValueError("Missing Windows package components")
        return manifest


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: python packaging/verify_source_release.py PACKAGE.zip")
    try:
        manifest = verify(Path(sys.argv[1]))
        print(f"Verified {len(manifest['files'])} files; version {manifest['version']}; commit {manifest['commit']}")
    except (ValueError, KeyError, OSError, zipfile.BadZipFile) as error:
        sys.exit(f"Verification failed: {error}")
