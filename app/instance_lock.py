"""One server process per local user directory; the OS releases the lock on exit."""
from __future__ import annotations

import errno
import json
import os
from pathlib import Path
import re

from storage import write_json


class InstanceLock:
    def __init__(self, folder: Path):
        self.folder = folder
        self.stream = None

    def acquire(self):
        if self.stream is not None:
            raise RuntimeError("Instance lock already acquired")
        self.folder.mkdir(parents=True, exist_ok=True)
        stream = (self.folder / ".instance.lock").open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                # Windows locks one byte. This file is never replaced or unlinked while in use.
                if os.fstat(stream.fileno()).st_size == 0:
                    stream.write(b"\0")
                    stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            stream.close()
            if error.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                return False
            raise
        except BaseException:
            stream.close()
            raise
        self.stream = stream
        return True

    def publish(self, version, instance, host, port=None):
        if self.stream is None:
            raise RuntimeError("Instance metadata requires the process lock")
        value = {"version": version, "instance": instance, "host": host, "port": port, "pid": os.getpid()}
        write_json(self.folder / ".instance.json", value)

    def close(self):
        if self.stream is not None:
            self.stream.close()
            self.stream = None


def read_instance(folder: Path):
    try:
        path = folder / ".instance.json"
        if path.stat().st_size > 8192:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(value, dict) or value.get("host") not in ("127.0.0.1", "localhost")
                or type(value.get("port")) is not int or not 1 <= value["port"] <= 65535
                or not isinstance(value.get("instance"), str) or not re.fullmatch(r"[a-f0-9]{32}", value["instance"])
                or not isinstance(value.get("version"), str) or len(value["version"]) > 80):
            return None
        return value
    except (OSError, ValueError, RecursionError):
        return None
