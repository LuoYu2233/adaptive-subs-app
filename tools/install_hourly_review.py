"""Install/remove only this project's hourly cron entry; preserve all other jobs."""
import argparse
from pathlib import Path
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
MARK = "# adaptive-subs-hourly-review"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--thread")
    ap.add_argument("--codex")
    ap.add_argument("--node")
    ap.add_argument("--remove", action="store_true")
    args = ap.parse_args()
    p = subprocess.run(["crontab", "-l"], text=True, capture_output=True)
    if p.returncode and "no crontab" not in p.stderr.lower():
        raise RuntimeError(p.stderr)
    folder = ROOT / "logs" / "hourly"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "crontab.before.txt").write_text(p.stdout)
    lines = [line for line in p.stdout.splitlines() if not line.endswith(MARK)]
    if not args.remove:
        if not all((args.thread, args.codex, args.node)):
            ap.error("--thread, --codex and --node are required")
        command = [str(ROOT / ".venv/bin/python"), str(ROOT / "tools/hourly_review.py"),
                   "--thread", args.thread, "--codex", args.codex, "--node", args.node, "--queue"]
        lines.append("0 * * * * " + shlex.join(command) + " >> " +
                     shlex.quote(str(folder / "cron.log")) + " 2>&1 " + MARK)
    subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n", text=True, check=True)
    print("Hourly review removed." if args.remove else "Hourly review installed at minute 00 of every hour.")


if __name__ == "__main__":
    main()
