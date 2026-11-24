"""Log capture for the windowed program (no console): everything printed goes to a log file and to a
small in-memory buffer that the control panel shows (/api/status). In the source folder with a console,
output still goes to the console too."""

from __future__ import annotations

import collections
import io
import os
import re
import sys
import threading
import time
from pathlib import Path

LINES: collections.deque = collections.deque(maxlen=200)
_lock = threading.Lock()
LOG_FILE: Path | None = None
MAX_LINE = 8192


def redact(text: str) -> str:
    """Best-effort masking for diagnostic text, before truncation or persistence."""
    text = str(text)
    for name in ("OPENAI_API_KEY", "DEEPSEEK_API_KEY", "MIMO_API_KEY"):
        key = os.environ.get(name, "")
        if key:
            text = text.replace(key, "[REDACTED]")
    text = re.sub(r"\bsk-[A-Za-z0-9_*-]{4,}", "[REDACTED]", text)
    text = re.sub(r"(?i)\bBearer\s+[^\s,;'\"}]+", "Bearer [REDACTED]", text)
    text = re.sub(r"(?i)(\b(?:api[_-]?key|access_token|refresh_token|authorization)\b[\"']?\s*[:=]\s*[\"']?)[^\s,;&\"'}]+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)(\b(?:set-cookie|cookie)\s*:\s*).*", r"\1[REDACTED]", text)
    return re.sub(r"([a-z][a-z0-9+.-]*://)[^/@\s]+:[^/@\s]+@", r"\1[REDACTED]@", text)


class _RotatingFile:
    """One active file plus one previous file, rotated during long-running sessions."""
    def __init__(self, path, max_bytes=2_000_000):
        self.path, self.max_bytes = path, max_bytes
        self.fh = open(path, "a", encoding="utf-8")
        self.size = path.stat().st_size

    def write(self, text):
        size = len(text.encode("utf-8"))
        if self.size and self.size + size > self.max_bytes:
            self.fh.close()
            self.path.replace(self.path.with_name(self.path.stem + ".old.log"))
            self.fh = open(self.path, "a", encoding="utf-8")
            self.size = 0
        self.fh.write(text)
        self.fh.flush()
        self.size += size

    def flush(self):
        self.fh.flush()

    def close(self):
        self.fh.close()


class _Tee(io.TextIOBase):
    def __init__(self, stream, fh):
        self.stream, self.fh, self.buf = stream, fh, ""
        self.discard = False

    def writable(self):
        return True

    def write(self, s):
        if not isinstance(s, str):
            s = str(s)
        with _lock:
            # A key may be printed across several write() calls; do not emit raw fragments.
            self.buf += s
            while "\n" in self.buf:
                line, self.buf = self.buf.split("\n", 1)
                if not self.discard:
                    self._emit(redact(line)[:MAX_LINE] + "\n")
                self.discard = False
            if len(self.buf) > 65536:
                if not self.discard:
                    self._emit("[日志行过长，已省略]\n")
                self.buf, self.discard = "", True
        return len(s)

    def _emit(self, safe):
        for target in (self.stream, self.fh):
            if target is not None:
                try:
                    target.write(safe)
                    target.flush()
                except Exception:
                    pass
        if safe.strip():
            LINES.append((time.strftime("%H:%M:%S"), safe.rstrip()))

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
    fh = _RotatingFile(LOG_FILE)
    fh.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
    sys.stdout = _Tee(sys.stdout, fh)
    sys.stderr = _Tee(sys.stderr, fh)


def tail(n: int = 40) -> list:
    with _lock:
        return list(LINES)[-n:]
