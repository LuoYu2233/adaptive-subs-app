"""Capture local engineering progress, then ask the existing Codex task to review it.

This never starts another agent, calls a model API, changes code or publishes a release.
Cron and the local Codex daemon must be available for the scheduled reminder to run.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def run(args, timeout=300):
    try:
        p = subprocess.run(args, cwd=ROOT, text=True, capture_output=True, timeout=timeout)
        return {"returncode": p.returncode, "output": (p.stdout + p.stderr)[-30000:]}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"returncode": -1, "output": str(exc)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--thread", required=True)
    ap.add_argument("--codex", required=True)
    ap.add_argument("--node", required=True)
    ap.add_argument("--queue", action="store_true")
    args = ap.parse_args()
    folder = ROOT / "logs" / "hourly"
    folder.mkdir(parents=True, exist_ok=True)
    if (folder / "PAUSED").exists():
        return 0
    with (folder / "monitor.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        now = datetime.now(timezone.utc)
        report = {
            "time_utc": now.isoformat(), "pid": os.getpid(),
            "status": run(["git", "status", "--short", "--branch"]),
            "commits": run(["git", "log", "-5", "--format=%h %cs %s"]),
            "diff": run(["git", "diff", "--stat"]),
            "checks": run([sys.executable, "tools/quality_check.py", "--node", args.node], timeout=360),
        }
        if (ROOT / "tools/browser_check.py").exists():
            report["browser"] = run([sys.executable, "tools/browser_check.py"], timeout=180)
        if (ROOT / "tools/instance_check.py").exists():
            report["instances"] = run([sys.executable, "tools/instance_check.py"], timeout=90)
        filename = folder / (now.strftime("%Y%m%dT%H%M%SZ") + ".json")
        filename.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        (folder / "latest.json").write_text(filename.read_text(encoding="utf-8"), encoding="utf-8")
        if args.queue:
            message = ("按用户授权执行每小时工程复盘：读取 " + str(filename) +
                       " 和 docs/ENGINEERING_PROGRESS.md；总结本小时完成、失败和风险，"
                       "根据测试与实际缺陷调整优先级，继续自主修复和验证 adaptive-subs-app。"
                       "这是同一持续任务的进度提醒，不要另起代理或重复之前的工作。"
                       "若用户已暂停或结束任务，停止此监控（运行 tools/install_hourly_review.py --remove）。")
            report["reminder"] = run([args.codex, "queue", "--thread", args.thread,
                                      "--message", message, "--resume"], timeout=45)
            filename.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            (folder / "latest.json").write_text(filename.read_text(encoding="utf-8"), encoding="utf-8")
        passed = all(report.get(key, {}).get("returncode", 0) == 0 for key in ("checks", "browser", "instances"))
        print(json.dumps({"report": str(filename), "checks_passed": passed,
                          "reminder": report.get("reminder")}, ensure_ascii=False))
        return 0 if passed and report.get("reminder", {}).get("returncode", 0) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
