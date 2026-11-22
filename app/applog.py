"""Log capture for the windowed program (no console): everything printed goes to a log file and to a
small in-memory buffer that the control panel shows (/api/status). In the source folder with a console,
output still goes to the console too."""

from __future__ import annotations

import collections
import io
import sys
import threading
import time
from pathlib import Path

LINES: collections.deque = collections.deque(maxlen=200)
_lock = threading.Lock()
LOG_FILE: Path | None = None


class _Tee(io.TextIOBase):
    def __init__(self, stream, fh):
        self.stream, self.fh, self.buf = stream, fh, ""

    def writable(self):
        return True

    def write(self, s):
        if not isinstance(s, str):
            s = str(s)
        with _lock:
            if self.stream is not None:
                try:
                    self.stream.write(s)
                except Exception:
                    pass
            if self.fh is not None:
                try:
                    self.fh.write(s)
                    self.fh.flush()
                except Exception:
                    pass
            self.buf += s
            while "\n" in self.buf:
                line, self.buf = self.buf.split("\n", 1)
                if line.strip():
                    LINES.append((time.strftime("%H:%M:%S"), line.rstrip()))
        return len(s)

    def flush(self):
        pass

    # behave like a normal text stream for code that tunes or inspects sys.stdout
    def reconfigure(self, **kw):
        if self.stream is not None and hasattr(self.stream, "reconfigure"):
            try:
                self.stream.reconfigure(**kw)
            except Exception:
                pass

    @property
    def encoding(self):
        return "utf-8"

    def isatty(self):
        return False


def install(log_dir: Path):
    """Send stdout / stderr to log_dir/adaptive-subs.log (kept to ~2 MB) and the in-memory buffer."""
    global LOG_FILE
    log_dir.mkdir(parents=True, exist_ok=True)
    LOG_FILE = log_dir / "adaptive-subs.log"
    if LOG_FILE.exists() and LOG_FILE.stat().st_size > 2_000_000:
        LOG_FILE.replace(log_dir / "adaptive-subs.old.log")
    fh = open(LOG_FILE, "a", encoding="utf-8", buffering=1)
    fh.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
    sys.stdout = _Tee(sys.stdout, fh)
    sys.stderr = _Tee(sys.stderr, fh)


def tail(n: int = 40) -> list:
    with _lock:
        return list(LINES)[-n:]
