"""Verify and launch a trusted local source ZIP using temporary data and no API keys.

Uses the current Python environment's dependencies. Integrity checks are not proof of
publisher identity: only run source archives you trust. Does not call providers.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location("verify_source_release", ROOT / "packaging/verify_source_release.py")
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    manifest = verifier.verify(args.archive)
    output = ROOT / "test-results/release"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="asubs-release-smoke-") as temp:
        folder = Path(temp)
        with zipfile.ZipFile(args.archive) as archive:
            archive.extractall(folder)  # Paths and entry types were checked by verify().
        source = next(folder.iterdir())
        env = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY")}
        env.update(ASUBS_HOME=str(folder / "home"), ASUBS_NO_BROWSER="1", ASUBS_HEADLESS="1", ANNOTATION_PROVIDER="deepseek")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        with (output / (args.archive.stem + ".log")).open("w", encoding="utf-8") as log:
            child = subprocess.Popen([sys.executable, "-u", str(source / "app/server.py"), "--port", str(port)],
                                     cwd=source, env=env, stdout=log, stderr=subprocess.STDOUT)
            try:
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                base = f"http://127.0.0.1:{port}"
                for _ in range(100):
                    if child.poll() is not None:
                        raise RuntimeError("Packaged server exited")
                    try:
                        with opener.open(base + "/api/health", timeout=1) as response:
                            health = json.load(response)
                        break
                    except OSError:
                        time.sleep(0.1)
                else:
                    raise RuntimeError("Packaged server did not become ready")
                if health["version"] != manifest["version"] or health["key"]:
                    raise RuntimeError("Unexpected release version or credentials")
                routes = ["/app", "/demo", "/profile", "/privacy", "/player.html", "/assets/app.js",
                          "/assets/learning.js", "/assets/profile-sync.js", "/assets/wordbook.js", "/assets/player-store.js"]
                for route in routes:
                    with opener.open(base + route, timeout=5) as response:
                        if response.status != 200 or not response.read():
                            raise RuntimeError(f"Packaged route unavailable: {route}")
                report = {"passed": True, "version": manifest["version"], "commit": manifest["commit"],
                          "key_configured": False, "routes": routes, "scope": "Source archive startup with existing dependencies"}
                (output / (args.archive.stem + ".json")).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
                print(json.dumps(report, indent=2))
            finally:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)


if __name__ == "__main__":
    main()
