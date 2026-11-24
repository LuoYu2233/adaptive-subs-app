"""Run the offline regression suite and JavaScript syntax checks."""
from __future__ import annotations

import argparse
from html.parser import HTMLParser
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


class InlineScripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts, self.current = [], None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script" and "src" not in attrs and attrs.get("type", "") in ("", "text/javascript", "module"):
            self.current = []

    def handle_data(self, data):
        if self.current is not None:
            self.current.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.current is not None:
            self.scripts.append("".join(self.current))
            self.current = None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--node", default=os.environ.get("ASUBS_NODE") or shutil.which("node"))
    args = parser.parse_args()
    commands = [[sys.executable, "-m", "pytest", "-q"], ["git", "diff", "--check"]]
    if not args.node:
        print("Node.js is required for JavaScript checks; set ASUBS_NODE or --node.")
        return 2
    commands.append([args.node, "--test", *map(str, sorted((ROOT / "tests").glob("*.cjs")))])
    for folder in ("extension", "app/web/assets"):
        commands.extend([args.node, "--check", str(p)] for p in sorted((ROOT / folder).glob("*.js")))
    failed = False
    for command in commands:
        result = subprocess.run(command, cwd=ROOT, check=False, timeout=300)
        failed |= result.returncode != 0
    with tempfile.TemporaryDirectory() as temp:
        for html in sorted((ROOT / "app/web").glob("*.html")):
            parser = InlineScripts()
            parser.feed(html.read_text(encoding="utf-8"))
            for i, source in enumerate(parser.scripts):
                script = Path(temp) / f"{html.stem}-{i}.mjs"
                script.write_text(source, encoding="utf-8")
                failed |= subprocess.run([args.node, "--check", str(script)], check=False).returncode != 0
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
