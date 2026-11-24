"""Measure task persistence using original synthetic subtitles and a disposable database.

This is a burst of progress updates, not a video processing or model latency benchmark.
No external requests, user database changes or provider calls are made.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import server as S
from task_store import TaskStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cues", type=int, default=6000)
    parser.add_argument("--updates", type=int, default=100)
    args = parser.parse_args()
    if not 1 <= args.cues <= 50000 or not 1 <= args.updates <= 10000:
        parser.error("cues must be 1–50000; updates must be 1–10000")
    report = {"timestamp": datetime.now(timezone.utc).isoformat(), "cues": args.cues,
              "updates": args.updates, "scope": "Synthetic burst of checkpoint writes; no model generation", "results": {}}
    with tempfile.TemporaryDirectory(prefix="asubs-checkpoint-bench-") as temp:
        for mode in ("forced", "coalesced"):
            S.TASK_STORE = TaskStore(Path(temp) / f"{mode}.sqlite3")
            writes = 0
            original_save = S.TASK_STORE.save
            def counted(*args):
                nonlocal writes
                writes += 1
                return original_save(*args)
            S.TASK_STORE.save = counted
            job = S.Job("synthetic")
            job.title, job.duration = "Original checkpoint fixture", args.cues * 5
            job.cues = [{"s": i * 5, "e": i * 5 + 5, "text": f"The bank approved request number {i}."} for i in range(args.cues)]
            job.ann = {i: [{"s": 4, "e": 8, "key": "bank", "kind": "word", "lv": 5000, "zh": "银行"}] for i in range(args.cues)}
            job.zh = {i: "银行批准了申请。" for i in range(args.cues)}
            start = time.monotonic()
            for i in range(args.updates):
                job.finished = i + 1
                if not job.checkpoint(force=mode == "forced"):
                    raise RuntimeError("Checkpoint failed")
            if not job.checkpoint():
                raise RuntimeError("Final checkpoint failed")
            elapsed = time.monotonic() - start
            persisted = S.TASK_STORE.load(job.vid)["payload"]["done"]
            assert persisted == args.updates
            report["results"][mode] = {"seconds": round(elapsed, 3), "writes": writes, "persisted_done": persisted}
    output = ROOT / "test-results/checkpoint-coalescing.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
