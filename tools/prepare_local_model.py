"""Verify or explicitly install the pinned optional speech model without using API keys.

python tools/prepare_local_model.py --install             # public download, about 148 MB
python tools/prepare_local_model.py --install --source DIR # verify an offline copy
python tools/prepare_local_model.py                       # verify the installed copy
An existing different/incomplete installation is preserved and reported, never overwritten.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / "packaging" / "local-model.json").read_text(encoding="utf-8"))
DEFAULT_PATH = ROOT / "app" / "models" / "faster-whisper-base.en"


def verify(path: Path, manifest=None):
    manifest = manifest or MANIFEST
    for name, expected in manifest["files"].items():
        file = path / name
        if not file.is_file() or file.stat().st_size != expected["bytes"]:
            raise ValueError(f"模型文件缺失或大小不符：{name}")
        with file.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != expected["sha256"]:
            raise ValueError(f"模型文件校验失败：{name}")


def install(destination=DEFAULT_PATH, source=None):
    destination = Path(destination)
    if destination.exists():
        verify(destination)  # A broken or user-supplied different model is never silently replaced.
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".asubs-model-", dir=destination.parent) as temp:
        staging = Path(temp) / "ready"
        staging.mkdir()
        if source is None:
            from huggingface_hub import snapshot_download
            source = Path(snapshot_download(MANIFEST["repo"], revision=MANIFEST["revision"],
                                           local_dir=Path(temp) / "download", token=False,
                                           allow_patterns=list(MANIFEST["files"])))
        for name in MANIFEST["files"]:
            shutil.copyfile(Path(source) / name, staging / name)
        verify(staging)
        (staging / "MODEL-SOURCE.json").write_text(json.dumps(MANIFEST, indent=2) + "\n", encoding="utf-8")
        os.rename(staging, destination)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", action="store_true", help="明确安装；未指定时只校验")
    parser.add_argument("--source", type=Path, help="离线模型来源目录")
    parser.add_argument("--destination", type=Path, default=DEFAULT_PATH)
    args = parser.parse_args()
    if args.source and not args.install:
        parser.error("--source 需要同时指定 --install")
    try:
        if args.install:
            install(args.destination, args.source)
        else:
            verify(args.destination)
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n原有目录保持不变；请选择完整的模型来源或新的安装目录。\n")
    print(f"模型校验通过：{args.destination}\n固定来源：{MANIFEST['repo']} @ {MANIFEST['revision']}")


if __name__ == "__main__":
    main()
