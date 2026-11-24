"""Exercise real local server processes with temporary data and no provider calls."""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def main():
    output = ROOT / "test-results/instance"
    output.mkdir(parents=True, exist_ok=True)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    children, streams = [], []
    report = {"scope": "Real local server process lifecycle; no model or platform requests"}
    with tempfile.TemporaryDirectory(prefix="asubs-instance-") as temp:
        home = Path(temp) / "home"
        home.mkdir()
        original = b'{"rev":0,"marks":{"known":[],"unknown":[]}}'
        (home / "profile.json").write_bytes(original)

        def free_port():
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                return sock.getsockname()[1]

        def start(folder, port, name):
            env = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY")}
            env.update(ASUBS_HOME=str(folder), ASUBS_NO_BROWSER="1", ASUBS_HEADLESS="1", ANNOTATION_PROVIDER="deepseek")
            log = (output / f"{name}.log").open("w", encoding="utf-8")
            streams.append(log)
            child = subprocess.Popen([sys.executable, "-u", str(ROOT / "app/server.py"), "--port", str(port)],
                cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
            children.append(child)
            return child

        def ready(child, folder):
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if child.poll() is not None:
                    raise RuntimeError(f"Expected owner exited with {child.returncode}")
                try:
                    info = json.loads((folder / ".instance.json").read_text())
                    if info["pid"] == child.pid and info["port"]:
                        with opener.open(f"http://{info['host']}:{info['port']}/api/health", timeout=.5) as response:
                            health = json.load(response)
                        if health["instance"] == info["instance"] and health["instance_locked"] is True:
                            assert health["key"] is False
                            return info
                except (OSError, ValueError, KeyError):
                    pass
                time.sleep(.1)
            raise RuntimeError("Server did not publish a verified instance")

        try:
            port = free_port()
            owner = start(home, port, "owner")
            first = ready(owner, home)
            database = (home / "tasks.sqlite3").read_bytes()
            repeated = start(home, free_port(), "repeated")
            assert repeated.wait(timeout=10) == 0
            assert owner.poll() is None
            assert (home / "profile.json").read_bytes() == original
            assert (home / "tasks.sqlite3").read_bytes() == database
            report["same_directory_different_port_reuses_owner"] = True

            separate = Path(temp) / "separate-home"
            other = start(separate, first["port"], "separate")
            other_info = ready(other, separate)
            assert other_info["port"] != first["port"] and other_info["instance"] != first["instance"]
            assert owner.poll() is None
            report["different_directory_has_independent_server"] = True

            owner.kill()
            owner.wait(timeout=5)
            assert (home / ".instance.lock").exists()
            replacement = start(home, port, "after-crash")
            current = ready(replacement, home)
            assert current["instance"] != first["instance"]
            assert (home / "profile.json").read_bytes() == original
            report["crash_releases_os_lock_without_deleting_files"] = True

            runtime_path = home / ".instance.json"
            intact = runtime_path.read_bytes()
            runtime_path.write_bytes(b"broken runtime metadata")
            blocked = start(home, free_port(), "bad-metadata")
            assert blocked.wait(timeout=10) != 0
            assert replacement.poll() is None
            assert runtime_path.read_bytes() == b"broken runtime metadata"
            assert (home / "profile.json").read_bytes() == original
            runtime_path.write_bytes(intact)
            report["bad_metadata_cannot_bypass_live_lock"] = True
            report["passed"] = True
            (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(report, indent=2))
        finally:
            for child in children:
                if child.poll() is None: child.terminate()
            for child in children:
                try: child.wait(timeout=5)
                except subprocess.TimeoutExpired: child.kill(); child.wait(timeout=5)
            for stream in streams: stream.close()


if __name__ == "__main__":
    main()
