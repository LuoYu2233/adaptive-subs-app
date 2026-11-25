"""Opt-in real CPU ASR smoke test, with original locally synthesized speech and no cloud API.

Requires the pinned model (tools/prepare_local_model.py --install) and eSpeak NG or eSpeak.
This tiny synthetic sample checks installation and inference, not real-world ASR accuracy.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
from unittest.mock import patch

from prepare_local_model import MANIFEST, DEFAULT_PATH, verify

ROOT = Path(__file__).resolve().parents[1]
CASES = {
    "bank": "The bank approved my loan. We walked along the river bank.",
    "lesson": "Learning a language takes practice. Please turn on the subtitles.",
    "travel": "We will meet at the station tomorrow morning. I would like a cup of coffee.",
}


def word_errors(expected, actual):
    reference, hypothesis = (re.findall(r"[a-z]+(?:'[a-z]+)?", text.lower()) for text in (expected, actual))
    row = list(range(len(hypothesis) + 1))
    for i, word in enumerate(reference, 1):
        next_row = [i]
        for j, heard in enumerate(hypothesis, 1):
            next_row.append(min(row[j] + 1, next_row[-1] + 1, row[j - 1] + (word != heard)))
        row = next_row
    return row[-1], len(reference)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_PATH)
    parser.add_argument("--espeak", default=shutil.which("espeak-ng") or shutil.which("espeak"))
    parser.add_argument("--output", type=Path, default=ROOT / "test-results/local-asr/check")
    args = parser.parse_args()
    if not args.espeak:
        parser.error("需要本机 eSpeak NG；通过 --espeak 指定程序路径")
    verify(args.model_dir)
    args.output.mkdir(parents=True, exist_ok=True)
    os.environ.update(LOCAL_ASR_MODEL_DIR=str(args.model_dir.resolve()), HF_HUB_OFFLINE="1",
                      HF_HUB_DISABLE_TELEMETRY="1")
    sys.path.insert(0, str(ROOT / "app"))
    import asr
    import local_asr
    import numpy as np

    attempts, cases = [], []
    def blocked(*args, **kwargs):
        attempts.append("network connection attempted")
        raise RuntimeError("Local recognition must not use the network")

    for name, expected in CASES.items():
        audio = args.output / f"{name}.wav"
        subprocess.run([args.espeak, "-v", "en-us", "-s", "150", "-w", str(audio), "--stdin"],
                       input=expected, text=True, capture_output=True, check=True, timeout=30)
        pcm = asr.decode(audio)
        with patch.object(socket.socket, "connect", blocked), patch.object(socket, "create_connection", blocked):
            started = time.monotonic()
            text = local_asr.transcribe(pcm, 0, len(pcm) / asr.SR)
            elapsed = time.monotonic() - started
        errors, words = word_errors(expected, text)
        cases.append({"name": name, "reference": expected, "transcript": text, "word_errors": errors,
                      "words": words, "wer": errors / words, "seconds": elapsed,
                      "audio_seconds": len(pcm) / asr.SR, "audio_sha256": hashlib.sha256(audio.read_bytes()).hexdigest()})
    with patch.object(socket.socket, "connect", blocked), patch.object(socket, "create_connection", blocked):
        silence = local_asr.transcribe(np.zeros(asr.SR * 3, dtype=np.int16), 0, 3)
    passed = not attempts and not silence and all(c["transcript"] and c["wer"] <= 0.30 for c in cases)
    report = {"timestamp": datetime.now(timezone.utc).isoformat(), "passed": passed,
              "scope": "Original synthetic speech smoke test; not a real-world accuracy benchmark",
              "model": {"repo": MANIFEST["repo"], "revision": MANIFEST["revision"]},
              "platform": platform.platform(), "python": platform.python_version(),
              "packages": {name: importlib.metadata.version(name) for name in ("faster-whisper", "ctranslate2", "av", "numpy")},
              "cpu_threads": local_asr._threads(), "cases": cases, "silence_transcript": silence,
              "network_attempts": attempts}
    report_path = args.output / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"Report: {report_path}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
