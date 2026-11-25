"""Verify a Windows ZIP's content manifest without running it or proving publisher identity."""
from pathlib import Path
import sys
import zipfile

from verify_source_release import verify


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: python packaging/verify_windows_release.py PACKAGE.zip")
    try:
        manifest = verify(Path(sys.argv[1]), kind="windows")
        print(f"Verified {len(manifest['files'])} files; version {manifest['version']}; commit {manifest['commit']}")
    except (ValueError, KeyError, OSError, zipfile.BadZipFile) as error:
        sys.exit(f"Verification failed: {error}")
