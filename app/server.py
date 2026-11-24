"""Adaptive Subs local server: the player + instant annotation of any YouTube video.

    .venv/Scripts/python app/server.py --open      # http://127.0.0.1:8765/player.html (browser, no window)
    .venv/Scripts/python app/main.py               # the program window (= AdaptiveSubs.exe, = start.bat)

Paste a YouTube link in the player:
  1. English subtitles are fetched (human-made preferred, auto-generated as fallback) and sent
     to the page within seconds, so the video can start right away with plain subtitles.
  2. Annotations and line translations are generated in small batches by several workers at once,
     always picking the batch closest to where the viewer is watching (the page reports seeks).
     Batches near the playhead use low reasoning effort for speed; the rest use medium.
  3. Each finished batch is pushed to the page over Server-Sent Events.
  4. When everything is done the result is saved to data/<id>.json (+ data/index.json),
     so the video opens instantly next time — also in the static GitHub Pages build.

The OpenAI key stays on this machine (.env); the page never sees it.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import socket
import sqlite3
import sys
import threading
import time
import traceback
import uuid

import numpy as np
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import annotate as A
import applog
import asr as ASR
import local_asr as LOCAL_ASR
import profile_store as PS
import providers as P
import video_catalog
from instance_lock import InstanceLock, read_instance
from storage import atomic_write, write_json
from task_store import TaskStore, TaskStoreCorrupt, TaskStoreConflict
from version import VERSION
from result_validation import valid_result

# Running from the source folder: everything lives here. As a packaged program (PyInstaller):
# the page files are inside the bundle (read-only) and settings / results go to %LOCALAPPDATA%.
FROZEN = bool(getattr(sys, "frozen", False))
BUNDLE = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))        # app/ in the source tree; the bundle when packaged
WEB = BUNDLE / "web"                                                  # pages + assets
BUNDLE_DATA = BUNDLE / "data"                                         # read-only data shipped with the program
# settings, results, cache: %LOCALAPPDATA%\AdaptiveSubs when packaged; the repository root when run from source
HOME = Path(os.environ["ASUBS_HOME"]) if os.environ.get("ASUBS_HOME") else (
    (Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "AdaptiveSubs") if FROZEN else BUNDLE.parent)
DATA = HOME / "data"
CONFIG = HOME / ("config.env" if FROZEN else ".env")                  # API keys + chosen model
PROFILE = PS.Store(HOME / "profile.json")                              # learner profile + known words
TASK_STORE = TaskStore(HOME / "tasks.sqlite3")
SEG = 12               # lines per batch: small batches -> first annotations sooner
WORKERS = 12           # parallel LLM requests per video (spare capacity for jumps)
URGENT_WINDOW = 90     # seconds ahead of the playhead handled with low effort (speed first)
ASR_PER_JOB = 4        # speech-recognition requests in flight per video (the API also has a per-minute cap)
CHECKPOINT_INTERVAL = 1.0  # seconds between background snapshots; explicit boundaries always flush
NLP_LOCK = threading.Lock()   # spaCy is not guaranteed thread-safe
CONFIG_LOCK = threading.RLock()
INSTANCE_ID = uuid.uuid4().hex
INSTANCE_GUARD = None



def client():
    return P.client()        # the annotation model chosen in the settings (providers.py)


def key_missing_msg() -> str:
    return f"还没有填写 {P.PROVIDERS[P.current()]['name']} 的 API key：请打开「程序设置」填写"


def saved_result(path: Path) -> dict | None:
    """Only complete, well-formed results may short-circuit a generation request."""
    try:
        if path.stat().st_size > 32 * 1024 * 1024:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if (valid_result(data)
                and not (data.get("source") == "asr" and any(ASR.is_refusal(c["text"]) for c in data["cues"]))):
            return data
    except (OSError, ValueError, RecursionError):
        pass
    return None


# ------------------------------------------------------------ subtitles ---

VIDEO_ID_RE = re.compile(r"(?:[?&]v=|youtu\.be/|/shorts/|/embed/|/live/)([A-Za-z0-9_-]{11})")
YT_ID_RE = re.compile(r"[A-Za-z0-9_-]{11}")


def parse_video_id(s: str) -> str | None:
    s = (s or "").strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", s):
        return s
    m = VIDEO_ID_RE.search(s)
    return m.group(1) if m else None


class NoSubtitles(Exception):
    pass


def parse_json3(raw: str) -> list[dict]:
    """YouTube json3 captions -> [{s, e, text}]. Works for human-made and auto-generated tracks
    (auto tracks roll and overlap, so each line ends when the next one starts)."""
    lines = []
    for ev in json.loads(raw).get("events", []):
        segs = ev.get("segs")
        if not segs or ev.get("aAppend"):
            continue
        text = A.clean("".join(s.get("utf8", "") for s in segs).replace("\n", " "))
        if not text or re.fullmatch(r"\[[^\]]*\]", text):      # [Music], [Applause]
            continue
        s = ev.get("tStartMs", 0) / 1000
        e = s + ev.get("dDurationMs", 0) / 1000
        # auto captions give each word's offset; end the line shortly after its last word,
        # so it disappears during silences instead of lingering until the next line
        offsets = [seg["tOffsetMs"] for seg in segs if "tOffsetMs" in seg]
        if offsets:
            e = min(e, s + max(offsets) / 1000 + 1.2)
        lines.append([s, e, text])
    lines.sort(key=lambda x: x[0])
    for i in range(len(lines) - 1):
        if lines[i + 1][0] > lines[i][0]:
            lines[i][1] = min(lines[i][1], lines[i + 1][0])
    return [{"s": round(s, 3), "e": round(max(e, s + 0.3), 3), "text": t} for s, e, t in lines]


class FetchFailed(Exception):
    """YouTube did not answer in time (network trouble), as opposed to the video having no subtitles."""


class VideoAccessError(Exception):
    def __init__(self, code: str, message: str, retry: bool):
        super().__init__(message)
        self.code, self.retry = code, retry


def video_access_error(error):
    """Turn platform restrictions into useful messages, without exposing yt-dlp CLI instructions."""
    text = str(error).lower()
    if "not a bot" in text:
        return VideoAccessError("platform_verification", "YouTube 要求验证访问身份，当前环境暂时无法匿名获取字幕。请稍后重试，或选择其他公开英语视频。", True)
    if re.search(r"http(?: error| status)?[ :]+429\b", text) or "too many requests" in text:
        return VideoAccessError("platform_rate_limit", "YouTube 暂时限制了请求频率。请稍后再试，连续重试可能延长限制。", True)
    if any(term in text for term in ("private video", "members-only", "join this channel", "confirm your age", "age-restricted", "login required")):
        return VideoAccessError("platform_login", "这个视频需要登录、年龄验证或会员权限，当前程序无法匿名获取字幕。请选择可公开访问的视频。", False)
    if any(term in text for term in ("video unavailable", "has been removed", "not available in your country")):
        return VideoAccessError("video_unavailable", "这个视频已不可用或在当前地区无法访问，请选择其他视频。", False)
    if re.search(r"http(?: error| status)?[ :]+403\b", text):
        return VideoAccessError("platform_denied", "YouTube 拒绝了当前字幕请求（HTTP 403）。请稍后重试，或选择其他公开英语视频。", True)
    return None


# A subtitle download normally takes well under a second, but YouTube's caption server now and then
# accepts the connection and never answers. Give up on such a request quickly and ask again.
NET_TIMEOUT = 10       # seconds without data before a request is abandoned
NET_TRIES = 3          # attempts per request
NET_ERR_RE = re.compile(r"timed? ?out|timeout|connection|connect|reset|refused|ssl|eof|getaddrinfo|"
                        r"name resolution|network|unreachable|10054|10060|http error (429|5\d\d)", re.I)


def short_err(e: Exception) -> str:
    msg = applog.redact(str(e)).splitlines()[0] if str(e) else e.__class__.__name__
    return re.sub(r"^(ERROR: )?(\[\w+\] )?([\w-]{11}: )?", "", msg)[:160]


def is_network_error(e: Exception) -> bool:
    if isinstance(e, (TimeoutError, ConnectionError)):
        return True
    return bool(NET_ERR_RE.search(f"{e.__class__.__name__} {e}"))


def with_retries(what: str, fn, on_retry=None, tries: int = NET_TRIES):
    last = None
    for attempt in range(1, tries + 1):
        try:
            return fn()
        except Exception as e:      # yt-dlp wraps timeouts in TransportError / DownloadError
            access = video_access_error(e)
            if access:
                raise access from e  # Restrictions and rate limits must not trigger rapid automatic retries.
            if not is_network_error(e):
                raise
            last = e
            text = str(e).lower()
            reason = ("超时" if re.search(r"timed? ?out|timeout|10060", text) else
                      "连接被断开" if re.search(r"reset|eof|10054|aborted", text) else short_err(e)[:80])
            print(f"  {what}：{reason}（第 {attempt}/{tries} 次）")
            if attempt < tries:
                if on_retry:
                    on_retry(f"连接 YouTube 较慢，正在重试{what}（第 {attempt + 1}/{tries} 次）…")
                time.sleep(attempt)
    raise FetchFailed(what) from last


class _QuietLogger:
    """Keep yt-dlp's own error lines out of the console; failures are reported by us, in one line."""
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


def fetch_subtitles(vid: str, on_retry=None) -> dict:
    import yt_dlp

    opts = {"quiet": True, "no_warnings": True, "skip_download": True,
            "socket_timeout": NET_TIMEOUT, "logger": _QuietLogger()}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = with_retries("读取视频信息", lambda: ydl.extract_info(
            f"https://www.youtube.com/watch?v={vid}", download=False), on_retry)
        failed = None
        for kind, tracks in (("manual", info.get("subtitles") or {}), ("auto", info.get("automatic_captions") or {})):
            # English tracks only; for auto captions prefer the original ASR track ("en-orig")
            keys = sorted((k for k in tracks if re.match(r"^en($|[-_])", k)),
                          key=lambda k: (k != "en-orig", k != "en", len(k)))
            for k in keys:
                fmt = next((f for f in tracks[k] if f.get("ext") == "json3"), None)
                if not fmt:
                    continue
                try:
                    # after one track has failed every attempt, give the others one try each
                    raw = with_retries("下载字幕", lambda: ydl.urlopen(fmt["url"]).read(), on_retry,
                                       tries=1 if failed else NET_TRIES)
                except FetchFailed as e:
                    failed = e          # this track keeps timing out: try the next one before giving up
                    continue
                cues = parse_json3(raw.decode("utf-8"))
                if cues:
                    return {"title": info.get("title") or vid, "duration": info.get("duration") or 0,
                            "source": kind, "cues": cues}
        if failed:
            raise failed
    raise NoSubtitles(vid)


# ----------------------------------------------------------------- jobs ---

class Job:
    def __init__(self, vid: str, sub: dict | None = None, save_dir: Path | None = None, asr_url: str | None = None):
        self.vid = vid
        self.sub = sub            # subtitles sent by the browser extension (else fetched from YouTube)
        self.save_dir = save_dir or DATA
        self.asr_url = asr_url    # no subtitles at all: recognise the speech of this video
        self.has_text = None      # ASR: per line, whether recognition is finished
        self.asr_inflight = 0
        self.asr_stats = {"ok": 0, "chinese": 0, "errors": 0, "last_error": ""}
        self.asr_usage: dict = {}
        self.feeding = False      # ASR: audio still arriving, more lines will be added
        self.sent = None          # ASR: lines already sent to the page (None: first "meta" not sent yet)
        self.seg_next = 0         # ASR: first line not yet in an annotation batch
        self.seg = None           # ASR: StreamSegmenter (the audio samples)
        self.audio_frac = 0.0     # ASR: share of the audio cut into lines so far
        self.aborted = False
        self.cond = threading.Condition()
        self.events: list[tuple[str, dict]] = []
        self.focus = 0.0          # where the viewer is (seconds)
        self.tasks: list[dict] = []
        self.total = 0
        self.finished = 0
        self.ann: dict[int, list] = {}
        self.zh: dict[int, str] = {}
        self.failed_tasks: list[dict] = []
        self.state = "running"
        self.started = time.time()
        self.first_ann_at = None
        self.usage: dict = {}     # tokens per step (annotate / translate)
        self.provider = P.current()
        self.model = P.model(self.provider)
        self.endpoint_id = P.endpoint_id(P.base_url(self.provider))
        self._client = None
        self._client_lock = threading.Lock()
        self.checkpoint_lock = threading.Lock()
        self.last_checkpoint_attempt = None
        self.persistence_error = False
        self.restored = None
        self.thread = None
        self.feeder = None
        self.asr_client = None
        self.asr_model = os.environ.get("ASR_MODEL", "mimo-v2.5-asr")
        self.asr_language = os.environ.get("ASR_LANGUAGE", "auto")
        self.asr_endpoint_id = P.endpoint_id(P.base_url("mimo"))

    def client(self):
        with self._client_lock:
            if self._client is None:
                self._client = P.client(self.provider)
            return self._client

    def checkpoint(self, *, force=True):
        with self.checkpoint_lock:
            now = time.monotonic()
            if not force and self.last_checkpoint_attempt is not None and now - self.last_checkpoint_attempt < CHECKPOINT_INTERVAL:
                return not self.persistence_error
            self.last_checkpoint_attempt = now
            with self.cond:
                payload = {
                    "version": 1, "title": getattr(self, "title", "") or (self.sub or {}).get("title", self.vid),
                    "sub": self.sub, "asr_url": self.asr_url,
                    "destination": "extension" if self.save_dir == EXT_DATA else "player",
                    "provider": self.provider, "model": self.model, "started": self.started,
                    "endpoint_id": self.endpoint_id, "asr_endpoint_id": self.asr_endpoint_id,
                    "asr_model": self.asr_model, "asr_language": self.asr_language,
                    "asr_usage": ASR.usage_snapshot(self.asr_usage), "asr_stats": dict(self.asr_stats),
                    "done": self.finished, "total": self.total,
                    "cues": [dict(c) for c in getattr(self, "cues", [])],
                    "duration": getattr(self, "duration", 0),
                    "ann": {str(k): [dict(n) for n in v] for k, v in self.ann.items()},
                    "zh": dict(self.zh), "usage": A.usage_snapshot(self.usage),
                    "failed_tasks": list(self.failed_tasks),
                }
                state = self.state
            try:
                TASK_STORE.save(self.vid, state, payload)
                self.persistence_error = False
                return True
            except (OSError, ValueError, sqlite3.Error) as exc:
                self.persistence_error = True
                print(f"[{self.vid}] 进度保存失败：{short_err(exc)}")
                return False

    def emit(self, typ: str, data: dict):
        with self.cond:
            self.events.append((typ, data))
            self.cond.notify_all()

    def progress_total(self) -> int:
        """Tasks in all. While speech recognition is still cutting lines, extrapolate from the audio so far."""
        if self.feeding and self.audio_frac > 0.02:
            return max(self.total, int(self.total / self.audio_frac))
        return self.total


JOBS: dict[str, Job] = {}
JOBS_LOCK = threading.Lock()
YT_SUBS: dict[str, dict] = {}     # YouTube subtitles fetched for the extension, waiting for "生成注释"
YT_SUBS_LOCK = threading.Lock()
MAX_ACTIVE_JOBS = 3


class JobBusy(RuntimeError):
    pass


def get_job(vid: str, start: bool, sub: dict | None = None, save_dir: Path | None = None,
            asr_url: str | None = None) -> Job | None:
    with JOBS_LOCK, CONFIG_LOCK:
        now = time.time()
        for key, old in list(JOBS.items()):
            if old.state in ("done", "partial", "error", "cancelled") and now - old.started > 1800:
                if old.thread is None or not old.thread.is_alive():
                    del JOBS[key]
        job = JOBS.get(vid)
        if job and job.state == "cancelling" and start:
            raise JobBusy("正在停止此前的请求，请稍后再试")
        if job and job.state in ("error", "partial", "cancelled") and start:
            if job.thread is not None and job.thread.is_alive():
                raise JobBusy("正在保存此前的进度，请稍后再试")
            job = None                         # retry a failed video
        if job is None and start:
            if sum(j.state in ("running", "cancelling") for j in JOBS.values()) >= MAX_ACTIVE_JOBS:
                raise JobBusy("同时最多处理 3 个视频，请先完成或取消一个任务")
            saved = TASK_STORE.load(vid)
            old = saved["payload"] if saved and saved["state"] != "done" else None
            if old:
                sub = sub or old.get("sub")
                asr_url = asr_url or old.get("asr_url")
                if save_dir is None:
                    save_dir = EXT_DATA if old.get("destination") == "extension" else DATA
            job = Job(vid, sub, save_dir, asr_url)
            job.restored = old
            if old and old.get("provider") == job.provider and old.get("model") == job.model and old.get("endpoint_id") == job.endpoint_id:
                job.usage = old.get("usage", {})
            if old and old.get("asr_model") == job.asr_model and old.get("asr_language") == job.asr_language and old.get("asr_endpoint_id") == job.asr_endpoint_id:
                job.asr_usage = old.get("asr_usage", {})
            # Freeze the client while creating the job so settings changes cannot
            # replace the key/provider for a job already in progress.
            job.client()
            if asr_url:
                job.asr_client = ASR.client()
            if old is None:
                if not job.checkpoint():
                    raise JobBusy("任务进度无法保存，请先检查控制台的任务记录或磁盘状态")
            else:
                # Preserve the entire old checkpoint until the worker merges it into the new job.
                TASK_STORE.save(vid, "running", old)
            job.thread = threading.Thread(target=run_job, args=(job,), daemon=True)
            JOBS[vid] = job
            job.thread.start()
        return job


def cancel_job(job: Job):
    with job.cond:
        if job.state != "running":
            return
        job.aborted = True
        job.state = "cancelling"
        job.tasks.clear()
        job.cond.notify_all()
    job.emit("status", {"msg": "正在停止；已发出的请求可能仍会完成，结果会保留。"})
    job.checkpoint()


def task_key(job: Job, t: dict):
    """Lower = sooner. Batches around and just after the playhead first; already-watched last.
    Within the same minute: speech recognition, then annotations, then translations."""
    cues, ids = job.cues, t.get("span") or t["ids"]
    s0, s1 = cues[ids[0]]["s"], cues[ids[-1]]["e"]
    f = job.focus
    d = 0.0 if s0 <= f <= s1 else (s0 - f if s0 > f else 100000 + (f - s1))
    return (int(d // 60), {"asr": 0, "ann": 1}.get(t["kind"], 2), d)


def run_job(job: Job):
    try:
        _run_job(job)
    finally:
        if job.feeder is not None:
            job.feeder.join()
        with job.cond:
            cancelled = job.state == "cancelling"
            if cancelled:
                job.state = "cancelled"
        if cancelled:
            job.emit("fail", {"msg": "任务已取消，已生成的内容已保留。", "cancelled": True, "retry": True})
        job.checkpoint()


def _run_job(job: Job):
    try:
        sub = job.sub
        old = job.restored
        if not job.asr_url and old and old.get("cues") and sub is None:
            sub = {"title": old["title"], "duration": old.get("duration", 0), "cues": old["cues"], "source": "restored"}
        if job.asr_url:
            sub = start_asr(job)        # returns once the first lines are cut; the rest keep coming
        elif sub is None:
            job.emit("status", {"msg": "正在获取英文字幕…"})
            sub = fetch_subtitles(job.vid, on_retry=lambda msg: job.emit("status", {"msg": msg}))
        if job.aborted:
            return
        job.title, job.duration, job.cues = sub["title"], sub["duration"], sub["cues"]
        if job.asr_url:
            # speech recognition: lines (and their tasks) are added by the audio thread as the audio is cut
            with job.cond:
                job.sent = len(job.cues)
                job.emit("meta", {"video": {"id": job.vid, "title": job.title, "duration": job.duration},
                                  "source": sub["source"], "cues": list(job.cues)})
        else:
            sub = {**sub, "cues": clean_ext_cues(sub["cues"])}
            job.cues = sub["cues"]
            job.sub = sub
            job.emit("meta", {"video": {"id": job.vid, "title": job.title, "duration": job.duration},
                              "source": sub["source"], "cues": job.cues})
            job.texts = [c["text"] for c in job.cues]
            if old and old.get("provider") == job.provider and old.get("model") == job.model and old.get("endpoint_id") == job.endpoint_id:
                old_texts = [c["text"] for c in old.get("cues", [])]
                same = {i for i in range(min(len(old_texts), len(job.texts)))
                        if A.context_item(old_texts, i) == A.context_item(job.texts, i)}
                job.ann = {int(k): v for k, v in old.get("ann", {}).items() if int(k) in same}
                job.zh = {int(k): v for k, v in old.get("zh", {}).items() if int(k) in same}
                job.usage = old.get("usage", {})
                if job.ann:
                    job.emit("ann", {str(k): v for k, v in job.ann.items()})
                if job.zh:
                    job.emit("zh", {str(k): v for k, v in job.zh.items()})
            with NLP_LOCK:
                job.cands = A.find_candidates(job.texts)
            segs = [list(range(i, min(i + SEG, len(job.texts)))) for i in range(0, len(job.texts), SEG)]
            with job.cond:
                if job.aborted:
                    return
                job.tasks = ([{"kind": "ann", "ids": [i for i in s if i not in job.ann]} for s in segs] +
                             [{"kind": "tr", "ids": [i for i in s if i not in job.zh and job.texts[i]], "retry": 0} for s in segs])
                job.tasks = [t for t in job.tasks if t["ids"]]
                job.total = len(job.tasks)
        job.checkpoint()
        print(f"[{job.vid}] {len(job.cues)} lines ({sub['source']} subtitles) in {time.time() - job.started:.1f}s: {job.title}")
        job.emit("progress", {"done": 0, "total": job.progress_total()})

        workers = [threading.Thread(target=worker, args=(job,), daemon=True) for _ in range(WORKERS)]
        for w in workers:
            w.start()
        for w in workers:
            w.join()
        if job.aborted:
            return
        finalize(job)
    except VideoAccessError as e:
        if job.state == "cancelling":
            return
        job.state = "error"
        job.emit("fail", {"msg": str(e), "retry": e.retry, "code": e.code})
    except ASR.AudioError as e:
        if job.state == "cancelling":
            return
        job.state = "error"
        print(f"[{job.vid}] {e}")
        job.emit("fail", {"msg": applog.redact(str(e))})
    except NoSubtitles:
        if job.state == "cancelling":
            return
        job.state = "error"
        print(f"[{job.vid}] no English subtitles")
        job.emit("fail", {"msg": "这个视频没有英文字幕（人工字幕和自动字幕都没有），暂时没法处理。", "nosubs": True})
    except FetchFailed as e:
        if job.state == "cancelling":
            return
        job.state = "error"
        print(f"[{job.vid}] 连不上 YouTube（{e}一直超时），网页上可以点「重试」")
        job.emit("fail", {"msg": f"连不上 YouTube：{e}超时，已自动重试 {NET_TRIES} 次。可能是网络或代理不稳定，请点「重试」。",
                          "retry": True})
    except Exception as e:
        if job.state == "cancelling":
            return
        if e.__class__.__name__ == "DownloadError":      # video private / removed / region-locked ...
            job.state = "error"
            print(f"[{job.vid}] YouTube refused: {short_err(e)}")
            job.emit("fail", {"msg": f"YouTube 拒绝了这个视频：{short_err(e)}"})
            return
        job.state = "error"
        traceback.print_exc()
        msg = short_err(e)
        job.emit("fail", {"msg": f"处理失败：{msg}"})


def pick_task(job: Job) -> dict | None:
    """Nearest task that can run now. With speech recognition, a batch of lines can be annotated or
    translated only once all of its lines are recognised."""
    best, best_key = None, None
    for t in job.tasks:
        if t["kind"] == "asr":
            if job.asr_inflight >= ASR_PER_JOB:
                continue
        elif job.has_text is not None:
            # the batch's lines, plus the line before and after it (context for punctuation and glosses)
            if job.feeding and t["ids"][-1] + 1 >= len(job.has_text):
                continue                          # the line after this batch isn't cut yet
            lo, hi = max(0, t["ids"][0] - 1), min(len(job.has_text), t["ids"][-1] + 2)
            if not all(job.has_text[lo:hi]):
                continue
        k = task_key(job, t)
        if best_key is None or k < best_key:
            best, best_key = t, k
    return best


def worker(job: Job):
    while True:
        with job.cond:
            while True:
                if (not job.tasks and not job.feeding) or job.aborted:
                    return
                t = pick_task(job)
                if t:
                    break
                job.cond.wait(timeout=1.0)          # waiting for speech recognition of these lines
            job.tasks.remove(t)
            if t["kind"] == "asr":
                job.asr_inflight += 1
            urgent = task_key(job, t)[2] < URGENT_WINDOW
        try:
            if t["kind"] == "asr":
                run_asr(job, t["ids"][0])
            elif t["kind"] == "ann":
                run_annotation(job, t["ids"], "low" if urgent else "medium")
            else:
                run_translation(job, t)
        except Exception as exc:
            if not job.aborted:
                traceback.print_exc()
                with job.cond:
                    job.failed_tasks.append({"kind": t["kind"], "ids": list(t["ids"]), "error": short_err(exc)})
        with job.cond:
            if t["kind"] == "asr":
                job.asr_inflight -= 1
                job.cond.notify_all()
            job.finished += 1
            done, total = job.finished, job.progress_total()
        job.emit("progress", {"done": done, "total": total, "failed": len(job.failed_tasks)})
        job.checkpoint(force=False)


# ---------------------------------------------------- speech recognition ---

def start_asr(job: Job) -> dict:
    """Speech recognition, streamed: a thread downloads and decodes the audio and cuts it into lines
    while it arrives (asr.stream_audio + StreamSegmenter). Lines and their tasks are added to the job
    as they are cut, so recognition starts within seconds even for a two-hour video.
    Returns once the first lines exist (or the audio is done / failed)."""
    if job.asr_client is None and not ASR.configured():
        raise ASR.AudioError("本地服务器没有配置 MIMO_API_KEY（.env），无法语音识别")
    job.cues, job.texts, job.cands, job.has_text, job.pieces = [], [], [], [], []
    job.feeding = True
    job.audio_error = None
    job.emit("status", {"msg": "正在获取音频地址…"})
    job.feeder = threading.Thread(target=feed_audio, args=(job,), daemon=True)
    job.feeder.start()
    with job.cond:
        while job.feeding and not job.cues and not job.audio_error and not job.aborted:
            job.cond.wait(timeout=1.0)
        if job.audio_error:
            raise ASR.AudioError(job.audio_error)
        if not job.cues and not job.aborted:
            raise ASR.AudioError("这个视频里没检测到人声")
    dur = job.sub.get("duration") or (job.seg.seconds if job.seg else 0)
    return {**job.sub, "duration": round(dur), "source": "asr", "cues": job.cues}


def feed_audio(job: Job):
    started = time.time()
    first_line = None

    def status(frac, speed):
        if job.sent is None:              # only before the first lines: afterwards the page shows progress
            job.emit("status", {"msg": f"正在下载音频 {frac * 100:.0f}%（{speed / 1024:.0f} KB/s）…"})

    gen = None
    try:
        gen = ASR.stream_audio(job.asr_url, on_status=status, cancelled=lambda: job.aborted)
        duration = next(gen)
        job.seg = ASR.StreamSegmenter(duration or job.sub.get("duration") or 0)
        for block in gen:
            if job.aborted:
                return
            if not isinstance(block, np.ndarray):
                continue
            new = job.seg.add(block)
            if duration:
                job.audio_frac = min(1.0, job.seg.seconds / duration)
            if new:
                add_asr_lines(job, new)
                if first_line is None:
                    first_line = time.time() - started
                    print(f"[{job.vid}] first {len(new)} lines after {first_line:.1f}s (audio so far {job.seg.seconds:.0f}s)")
        add_asr_lines(job, job.seg.finish(), final=True)
        print(f"[{job.vid}] audio {job.seg.seconds / 60:.1f} min -> {len(job.cues)} lines, "
              f"cut in {time.time() - started:.1f}s")
    except ASR.AudioError as e:
        job.audio_error = str(e)
    except Exception as e:
        traceback.print_exc()
        job.audio_error = f"音频处理失败：{short_err(e)}"
    finally:
        if gen is not None:
            gen.close()                   # stops the download if it is still running (aborted)
        with job.cond:
            job.feeding = False
            job.audio_frac = 1.0
            if job.audio_error and job.sent is not None and not job.aborted:
                # failed midway: keep what was recognised so far, stop waiting for more
                print(f"[{job.vid}] audio stopped early: {job.audio_error}")
                job.emit("status", {"msg": f"{job.audio_error}（已识别的部分照常显示）"})
                add_asr_lines(job, [], final=True)
            job.cond.notify_all()


def add_asr_lines(job: Job, pieces: list, final: bool = False):
    """Append newly cut lines: their recognition tasks, full annotation batches, and a "more" event."""
    with job.cond:
        if job.aborted:
            return
        start = len(job.cues)
        for s, e in pieces:
            if job.cues and job.cues[-1]["e"] > s:            # a line ends where the next begins
                job.cues[-1]["e"] = round(max(job.cues[-1]["s"] + 0.3, s), 2)
            job.cues.append({"s": s, "e": round(e + 0.3, 2), "text": ""})
            job.pieces.append((s, e))
            job.texts.append("")
            job.cands.append(None)
            job.has_text.append(False)
        n = len(job.cues)
        new_tasks = []
        for i in range(start, n):
            b = i // SEG
            # ranked by the batch it belongs to: a batch near the viewer gets all its lines recognised together
            new_tasks.append({"kind": "asr", "ids": [i], "span": list(range(b * SEG, min((b + 1) * SEG, n)))})
        while job.seg_next < n and (n - job.seg_next >= SEG or final):
            ids = list(range(job.seg_next, min(job.seg_next + SEG, n)))
            new_tasks += [{"kind": "ann", "ids": ids}, {"kind": "tr", "ids": ids, "retry": 0}]
            job.seg_next = ids[-1] + 1
        job.tasks += new_tasks
        job.total += len(new_tasks)
        if job.sent is not None and n > job.sent:
            job.emit("more", {"from": job.sent, "cues": job.cues[job.sent:n]})
            job.sent = n
        job.cond.notify_all()


def run_asr(job: Job, i: int):
    text, err = "", None
    before, after = ASR.pads(job.pieces, i)
    try:
        try:
            text = ASR.transcribe(job.seg.view(), *job.pieces[i], job.asr_usage, before, after,
                                  client_instance=job.asr_client, model=job.asr_model, language=job.asr_language,
                                  cancelled=lambda: job.aborted)
        except ASR.ContentRefused:
            text = local_fallback(job.seg.view(), job.pieces[i], before, after, f"{job.vid} #{i}")
    except Exception as e:
        err = short_err(e)
    chinese = bool(text) and ASR.mostly_chinese(text)
    if chinese or not re.search(r"[A-Za-z]", text):     # Chinese, or fillers like "嗯。"
        text = ""
    abort = None
    with job.cond:
        st = job.asr_stats
        if err:
            st["errors"] += 1
            st["last_error"] = err
        elif chinese:
            st["chinese"] += 1
        elif text:
            st["ok"] += 1
        job.texts[i] = job.cues[i]["text"] = text
        job.has_text[i] = True
        if not job.aborted:
            if st["errors"] >= 5 and st["ok"] == 0:
                abort = f"语音识别接口出错：{st['last_error']}"
            elif st["chinese"] >= 6 and st["chinese"] > 1.5 * st["ok"]:
                abort = "这个视频说的主要是中文，不是英语，已停止语音识别"
            if abort:
                job.aborted = True
                job.state = "error"
                job.tasks.clear()
        job.cond.notify_all()
    if abort:
        print(f"[{job.vid}] stopped: {abort}")
        job.emit("fail", {"msg": abort})
    elif text:
        job.emit("text", {str(i): text})


def local_fallback(pcm, piece, before: float, after: float, label: str) -> str:
    """Use local Whisper when the cloud recogniser declines a line; failures remain explicit."""
    if not LOCAL_ASR.available():
        raise ASR.AudioError("云端未识别这一句，且未安装本地语音模型；该句需要重试")
    try:
        t = time.time()
        text = LOCAL_ASR.transcribe(pcm, *piece, before, after)
        print(f"[{label}] refused by the speech-recognition filter -> local model ({time.time() - t:.1f}s): {text[:60]}")
        return ASR.fix_i(ASR.strip_tags(text))
    except Exception as e:
        print(f"[{label}] local speech model failed: {short_err(e)}")
        raise ASR.AudioError("本地语音识别失败，该句需要重试") from e


REPAIRING: set = set()
REPAIR_LOCK = threading.Lock()


def tag_only_lines(data: dict) -> list[int]:
    """Speech-recognition lines that are only non-speech labels ("<chinese> <chinese>", "[Music]")."""
    if data.get("source") != "asr":
        return []              # uploaders' subtitles keep their brackets ("[in Portuguese]")
    return [i for i, c in enumerate(data.get("cues", [])) if c.get("text") and not ASR.strip_tags(c["text"])]


def refusal_lines(data: dict) -> list[int]:
    return [i for i, c in enumerate(data.get("cues", [])) if ASR.is_refusal(c.get("text", ""))]


def bili_page(bvid: str, cid: str) -> int:
    """Part number of a multi-part Bilibili video from its cid (public API, no login)."""
    import urllib.request
    req = urllib.request.Request(f"https://api.bilibili.com/x/player/pagelist?bvid={bvid}",
                                 headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.bilibili.com/"})
    with urllib.request.urlopen(req, timeout=10) as r:
        pages = json.loads(r.read().decode("utf-8")).get("data") or []
    return next((p["page"] for p in pages if str(p.get("cid")) == cid), 1)


def asr_source_url(vid: str) -> str | None:
    """The page whose audio a speech-recognition result came from (Bilibili part or YouTube video)."""
    m = re.fullmatch(r"bili_(BV[0-9A-Za-z]{10})_(\d+)_asr", vid)
    if m:
        return f"https://www.bilibili.com/video/{m[1]}/?p={bili_page(m[1], m[2])}"
    m = re.fullmatch(r"yt_([A-Za-z0-9_-]{11})_asr", vid)
    return f"https://www.youtube.com/watch?v={m[1]}" if m else None


def repair_saved(vid: str, path: Path):
    """A video recognised before refusals were detected has the filter's refusal sentence as some lines.
    Download the audio up to the last such line, recognise those lines with the local model, annotate and
    translate them, and save — the next time the video opens they are fixed (now they show blank)."""
    with REPAIR_LOCK:
        if vid in REPAIRING:
            return
        REPAIRING.add(vid)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        cues, bad = data["cues"], refusal_lines(data)
        fixed = {}
        url = asr_source_url(vid) if LOCAL_ASR.available() else None
        if url:
            print(f"[{vid}] repairing {len(bad)} lines the speech filter refused (local model)")
            need = max(cues[i]["e"] for i in bad) + 2
            gen = ASR.stream_audio(url)
            seg = ASR.StreamSegmenter(next(gen) or 0)
            for block in gen:
                if isinstance(block, np.ndarray):
                    seg.add(block)
                if seg.seconds > need:
                    break
            gen.close()
            for i in bad:
                s, e = cues[i]["s"], max(cues[i]["s"] + 0.3, cues[i]["e"] - 0.3)     # cue ends were padded by 0.3 s
                fixed[i] = local_fallback(seg.view(), (s, e), 0.15, 0.15, f"{vid} #{i}")
        job = Job(vid, sub=None, save_dir=EXT_DATA)
        job.cues, job.texts = cues, [("" if i in bad else c["text"]) for i, c in enumerate(cues)]
        for i, t in fixed.items():
            job.texts[i] = t
        job.cands = [None] * len(cues)
        job.first = {(n["key"], n["kind"]): n for c in cues for n in c.get("notes", [])}
        ids = [i for i in bad if job.texts[i]]
        if ids and P.has_key():
            run_annotation(job, ids, "low")
            run_translation(job, {"ids": ids, "retry": 1})
        for i in bad:
            cues[i].update(text=job.texts[i], notes=job.ann.get(i, []), zh=job.zh.get(i, ""))
        write_json(path, data)
        print(f"[{vid}] repaired: {sum(1 for i in bad if job.texts[i])} of {len(bad)} lines recognised again")
    except Exception as e:
        print(f"[{vid}] repair failed: {short_err(e)}")
    finally:
        with REPAIR_LOCK:
            REPAIRING.discard(vid)


PUNCT_PROMPT = """You fix punctuation in subtitles made by speech recognition.
Each line was recognised on its own, so lines often start with a capital letter and end with a period
even when the sentence goes on in the next line.
Return every line in `lines` with ONLY capitalisation and punctuation corrected, so that consecutive
lines read as continuous speech. Example: "Has too often become synonymous." + "With man-hating."
-> "has too often become synonymous" + "with man-hating."
Keep a sentence end (period, question mark) where a sentence really is complete and the next line
starts a new one; e.g. "...this has to stop." + "For the record, ..." keeps the period and the capital.
Rules: never add, remove, reorder or change words; keep "I" and proper nouns capitalised; a line that
continues into the next one ends with no punctuation or a comma. `prev` and `next` are context only.
Return JSON: {"items": [{"id": <id>, "text": "<line>"}]}"""


def fix_asr_punctuation(job: Job, ids: list[int]):
    """Speech recognition: repair sentence punctuation across line breaks (words stay exactly the same)."""
    def words(s):
        return re.findall(r"[a-z0-9']+", s.lower().replace("’", "'"))

    payload = {"prev": job.texts[ids[0] - 1] if ids[0] > 0 else "",
               "lines": [{"id": i, "text": job.texts[i]} for i in ids],
               "next": job.texts[ids[-1] + 1] if ids[-1] + 1 < len(job.texts) else ""}
    try:
        data = A.call_json(job.client(), job.model, "none", PUNCT_PROMPT, payload, usage=job.usage,
                           cancelled=lambda: job.aborted)
    except Exception as e:
        print(f"[{job.vid}] punctuation pass failed: {short_err(e)}")
        return
    changed = {}
    for it in data.get("items", []):
        try:
            i, text = int(it["id"]), A.clean(str(it["text"]))
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if i in ids and text != job.texts[i] and words(text) == words(job.texts[i]):
            changed[i] = text
    if changed:
        with job.cond:
            for i, text in changed.items():
                job.texts[i] = job.cues[i]["text"] = text
        job.emit("text", {str(i): t for i, t in changed.items()})


def run_annotation(job: Job, ids: list[int], effort: str):
    empty = [i for i in ids if not job.texts[i]]          # silence / music (speech recognition)
    ids = [i for i in ids if job.texts[i]]
    if empty:
        with job.cond:
            for i in empty:
                job.ann[i] = []
        job.emit("ann", {str(i): [] for i in empty})
    if not ids:
        return
    if job.asr_url:
        fix_asr_punctuation(job, ids)
    todo = [i for i in ids if job.cands[i] is None]
    if todo:
        with NLP_LOCK:
            for i, c in zip(todo, A.find_candidates([job.texts[i] for i in todo])):
                job.cands[i] = c
    payload = []
    for i in ids:
        it = A.context_item(job.texts, i)
        it["candidates"] = [{"word": c["word"], "rank": c["rank"]} for c in job.cands[i]]
        payload.append(it)
    data = A.call_json(job.client(), job.model, effort, A.ANNOTATE_PROMPT, payload, usage=job.usage,
                       cancelled=lambda: job.aborted)
    by_id = {}
    for it in data.get("items", []):
        try:
            by_id[int(it["id"])] = it.get("notes", [])
        except (KeyError, TypeError, ValueError, OverflowError):
            pass
    # second chance for rare words the model skipped (misspellings, everyday words it underrated)
    placed = {i: A.place_notes(job.texts[i], by_id.get(i, [])) for i in ids}
    missing = A.missing_candidates(job.texts, ids, job.cands, placed)
    if missing:
        extra = A.gloss_missing(job.client(), job.model, "low", job.texts, missing, usage=job.usage,
                                cancelled=lambda: job.aborted)
        for i, ns in extra.items():
            placed[i] = A.place_notes(job.texts[i], by_id.get(i, []) + ns)
    chunk = [{"i": i, "text": job.texts[i], "notes": placed[i]} for i in ids]
    with NLP_LOCK:
        A.add_keys(chunk)
    A.blend_word_levels(chunk)
    with job.cond:
        for c in chunk:
            job.ann[c["i"]] = c["notes"]
        if job.first_ann_at is None:
            job.first_ann_at = time.time()
            print(f"[{job.vid}] first annotations after {job.first_ann_at - job.started:.1f}s")
    job.emit("ann", {str(c["i"]): c["notes"] for c in chunk})


def run_translation(job: Job, t: dict):
    def words(s, n=3):
        return " ".join(re.findall(r"[a-z0-9']+", s.lower().replace("’", "'"))[:n])

    t = {**t, "ids": [i for i in t["ids"] if job.texts[i]]}
    if not t["ids"]:
        return
    data = A.call_json(job.client(), job.model, "low", A.TRANSLATE_PROMPT, [A.context_item(job.texts, i) for i in t["ids"]],
                         usage=job.usage, cancelled=lambda: job.aborted)
    got = {}
    for it in data.get("items", []):
        try:
            i = int(it["id"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if i in t["ids"] and str(it.get("zh", "")).strip() and words(str(it.get("src", ""))) == words(job.texts[i]):
            got[i] = re.sub(r"^\s*>>\s*", "", str(it["zh"]).strip())     # some models copy the ">>" speaker mark
    with job.cond:
        job.zh.update(got)
        failed = [i for i in t["ids"] if i not in got]
        if failed and t["retry"] == 0 and not job.aborted:          # misaligned lines: retry once in tiny batches
            for k in range(0, len(failed), 4):
                job.tasks.append({"kind": "tr", "ids": failed[k:k + 4], "retry": 1})
                job.total += 1
    if got:
        job.emit("zh", {str(i): z for i, z in got.items()})
    if failed and t["retry"]:
        raise A.ModelResponseError(f"{len(failed)} 行翻译未成功对齐，请重试")


def finalize(job: Job):
    missing_ann = [i for i, c in enumerate(job.cues) if c["text"] and i not in job.ann]
    missing_zh = [i for i, c in enumerate(job.cues) if c["text"] and i not in job.zh]
    incomplete = bool(missing_ann or missing_zh or job.failed_tasks or getattr(job, "audio_error", None)
                      or job.asr_stats["errors"])
    cues = [{"s": c["s"], "e": c["e"], "text": c["text"], "zh": job.zh.get(i, ""), "notes": job.ann.get(i, [])}
            for i, c in enumerate(job.cues) if c["text"]]
    A.unify_glosses(cues)   # final pass in video order
    data = {"schema_version": 2, "status": "partial" if incomplete else "complete",
            "missing": {"annotations": missing_ann, "translations": missing_zh},
            "video": {"id": job.vid, "title": job.title, "duration": job.duration or (cues[-1]["e"] if cues else 0)},
            "generated": time.strftime("%Y-%m-%d"), "model": job.model, "provider": job.provider,
            "usage": {**job.usage, "total": A.usage_total(job.usage), **({"asr": job.asr_usage} if job.asr_usage else {})},
            "asr_model": job.asr_model if job.asr_url else None,
            "source": "asr" if job.asr_url else (job.sub or {}).get("source", ""), "cues": cues}
    job.save_dir.mkdir(parents=True, exist_ok=True)
    write_json(job.save_dir / f"{job.vid}.json", data)
    catalog_warning = ""
    if job.save_dir == DATA and not incomplete:
        try:
            A.update_index(DATA, job.vid, job.title, f"{job.vid}.json")
        except (OSError, ValueError):
            catalog_warning = "字幕已保存，视频目录暂时无法写入；本地播放器仍可从字幕文件读取视频。"
            print(f"[{job.vid}] {catalog_warning}")
    with job.cond:
        if job.aborted:
            return
        job.state = "partial" if incomplete else "done"
    secs = time.time() - job.started
    tot = A.usage_total(job.usage)
    cost = P.cost_usd(job.provider, tot, job.model, job.endpoint_id)
    cost_label = f"≈ ${cost:.3f}（估算）" if cost is not None else "费用以服务商账单为准"
    print(f"[{job.vid}] done in {secs:.0f}s, {sum(len(c['notes']) for c in cues)} annotations, "
          f"tokens: input {tot['input']:,} (cached {tot['cached_input']:,}), output {tot['output']:,} "
          f"(of which reasoning {tot['reasoning']:,}), {tot['calls']} API calls, {job.model} {cost_label}")
    if job.asr_usage:
        sec = job.asr_usage.get("asr_seconds", 0)
        price_label = f" (about ¥{ASR.cost_cny(sec):.2f})" if job.asr_model == "mimo-v2.5-asr" and job.asr_endpoint_id == P.endpoint_id(P.default_base_url("mimo")) else ""
        print(f"[{job.vid}] speech recognition: {job.asr_usage.get('asr_calls', 0)} requests, "
              f"{sec / 60:.1f} min billed{price_label}")
    job.seg = None                 # free the audio samples
    if incomplete:
        job.emit("fail", {"msg": "部分字幕处理失败；已生成的内容已保存，重试会复用成功的缓存。",
                          "retry": True, "partial": True, "missing": data["missing"]})
        return
    job.emit("done", {"file": f"{job.vid}.json", "seconds": round(secs), "usage": data["usage"], "warning": catalog_warning})


# ------------------------------------------------- browser extension API ---
# The extension (extension/) reads subtitles inside the Bilibili page, where the viewer is logged in,
# and sends them here for annotation. Its requests carry a custom header: a web page cannot add one
# without a CORS preflight, which this server never approves, so other websites cannot start paid jobs.
EXT_DATA = DATA / "extension"      # results for extension videos (not listed in the player)
EXT_HEADER = "X-Adaptive-Subs"
EXT_ID_RE = re.compile(r"[A-Za-z0-9_.-]{1,80}")
MAX_CUES = 6000
MAX_CUE_CHARS = 2000
MAX_SUBTITLE_CHARS = 1_000_000


class SubtitleLimit(ValueError):
    pass


def clean_ext_cues(raw) -> list[dict]:
    if isinstance(raw, list) and len(raw) > MAX_CUES:
        raise SubtitleLimit(f"字幕超过 {MAX_CUES} 行，请选择较短的视频")
    cues = []
    total_chars = 0
    for c in raw if isinstance(raw, list) else []:
        try:
            s, e = float(c["s"]), float(c["e"])
            text = A.clean(str(c["text"]))
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if text and math.isfinite(s) and math.isfinite(e) and e >= s >= 0 and not re.fullmatch(r"[\[(（【][^\])）】]*[\])）】]", text):
            total_chars += len(text)
            if len(text) > MAX_CUE_CHARS or total_chars > MAX_SUBTITLE_CHARS:
                raise SubtitleLimit("字幕文本过长，请使用已正确分句的字幕或选择较短的视频")
            cues.append({"s": round(s, 3), "e": round(max(e, s + 0.3), 3), "text": text})
    cues.sort(key=lambda c: c["s"])
    return cues


# ----------------------------------------------------------------- http ---

class Server(ThreadingHTTPServer):
    """Refuse to share a port. Python's HTTP server sets SO_REUSEADDR, which on Windows lets a
    second server bind a port that is already in use, silently: the browser keeps talking to the
    old one. Exclusive binding makes a busy port fail, so we can move to a free one."""
    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):          # Windows
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


EXPLAIN_PROMPT = A.EXPLAIN_PROMPT

STATIC_PAGES = {"/": "player.html", "/player.html": "player.html", "/setup": "setup.html", "/setup.html": "setup.html",
                "/profile": "profile.html", "/profile.html": "profile.html", "/app": "app.html", "/app.html": "app.html",
                "/demo": "demo.html", "/demo.html": "demo.html", "/privacy": "privacy.html", "/privacy.html": "privacy.html"}
LAST_EXT = 0.0            # when the browser extension last talked to us (shown in the control panel)
STARTED = time.time()
LOCAL_HOST_RE = re.compile(r"(127\.0\.0\.1|localhost|\[::1\])(:\d+)?")
CONFIG_KEYS = ("ANNOTATION_PROVIDER", "OPENAI_API_KEY", "DEEPSEEK_API_KEY", "MIMO_API_KEY",
               "OPENAI_MODEL", "DEEPSEEK_MODEL", "MIMO_MODEL", "ASR_MODEL")


def save_config(updates: dict):
    with CONFIG_LOCK:
        _save_config(updates)


def _save_config(updates: dict):
    """Write settings into the config file, keeping every other line as it is."""
    lines = CONFIG.read_text(encoding="utf-8").splitlines() if CONFIG.exists() else []
    out, seen = [], set()
    for ln in lines:
        k = ln.split("=", 1)[0].strip()
        if k in updates:
            seen.add(k)
            if updates[k]:
                out.append(f"{k}={updates[k]}")
        else:
            out.append(ln)
    out += [f"{k}={v}" for k, v in updates.items() if k not in seen and v]
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(CONFIG, "\n".join(out) + "\n")
    for k, v in updates.items():
        if v:
            os.environ[k] = v
        else:
            os.environ.pop(k, None)
    P.reset_clients()
    ASR.reset_client()


def key_hint(name: str) -> str:
    k = os.environ.get(name, "")
    return f"已填写（…{k[-4:]}）" if k else ""


def extension_dir() -> Path:
    """The browser extension folder: next to the program in the release zip, or in the source folder."""
    if FROZEN:
        return Path(sys.executable).resolve().parent.parent / "浏览器插件"
    return Path(__file__).resolve().parent.parent / "extension"


def recent_videos(limit: int = 8) -> list:
    """Videos finished before (Bilibili extension + player), newest first. Only the head of each file is read."""
    out = []
    for folder, kind in ((EXT_DATA, "bili"), (DATA, "youtube")):
        if not folder.exists():
            continue
        for f in folder.glob("*.json"):
            if f.name == "index.json":
                continue
            try:
                with f.open(encoding="utf-8") as stream:
                    head = stream.read(600)
                modified = f.stat().st_mtime
                m = re.search(r'"title":"((?:[^"\\]|\\.)*)"', head) or re.search(r'"title":\s*"((?:[^"\\]|\\.)*)"', head)
                if not m or '"video"' not in head:      # not a processed video (word lists, test bank ...)
                    continue
                title = json.loads(f'"{m.group(1)}"')
            except (OSError, ValueError):
                continue
            vid = f.stem
            bv = re.match(r"bili_(BV[0-9A-Za-z]{10})", vid)
            yt = re.fullmatch(r"yt_([A-Za-z0-9_-]{11})_asr", vid)
            url = (f"https://www.bilibili.com/video/{bv.group(1)}" if bv else
                   f"https://www.youtube.com/watch?v={yt.group(1) if yt else vid}" if yt or kind == "youtube" else "")
            out.append({"id": vid, "title": title, "url": url, "asr": vid.endswith("_asr"),
                        "mtime": modified})
    out.sort(key=lambda x: -x["mtime"])
    return out[:limit]


def app_status(port: int) -> dict:
    with JOBS_LOCK:
        jobs = list(JOBS.values())
    running = []
    for j in jobs:
        if j.state != "running" and time.time() - j.started > 1800:
            continue
        running.append({"id": j.vid, "title": getattr(j, "title", "") or j.vid, "state": j.state,
                        "done": j.finished, "total": j.progress_total() if j.state == "running" else j.total,
                        "asr": bool(j.asr_url), "feeding": j.feeding, "started": j.started,
                        "persistence_error": j.persistence_error})
    live_ids = {j.vid for j in jobs}
    task_issue = None
    try:
        saved_tasks = TASK_STORE.recent(summary=True)
    except TaskStoreCorrupt as error:
        saved_tasks = []
        task_issue = {"error": str(error), "recoverable": bool(error.fingerprint), "fingerprint": error.fingerprint}
    except (OSError, sqlite3.Error):
        saved_tasks = []
        task_issue = {"error": "任务记录暂时无法读取，请检查磁盘空间、文件权限或其他占用程序", "recoverable": False}
    for saved in saved_tasks:
        if saved["id"] in live_ids or saved["state"] == "done":
            continue
        p = saved["payload"]
        running.append({"id": saved["id"], "title": p.get("title") or saved["id"],
                        "state": "interrupted" if saved["state"] in ("running", "cancelling") else saved["state"],
                        "done": p.get("done", 0), "total": p.get("total", 0), "asr": bool(p.get("asr_url")),
                        "feeding": False, "started": p.get("started", saved["updated"]), "persistence_error": False})
    running.sort(key=lambda x: -x["started"])
    profile_error = None
    try:
        prof = PROFILE.payload()
    except (PS.ProfileCorrupt, OSError) as error:
        profile_error = str(error) if isinstance(error, PS.ProfileCorrupt) else "学习档案无法读取，请检查文件权限"
        prof = {"setup": False, "level": 3000, "test": None, "lists": [],
                "stats": {"known": 0, "marked_known": 0, "marked_unknown": 0}}
    return {
        "version": VERSION, "port": port, "uptime": int(time.time() - STARTED),
        "provider": P.PROVIDERS[P.current()]["name"], "model": P.model(), "key": P.has_key(),
        "asr": ASR.configured(), "local_asr": LOCAL_ASR.available(),
        "ext_seen": int(time.time() - LAST_EXT) if LAST_EXT else None,
        "profile": {k: prof[k] for k in ("setup", "level", "test", "lists")} | {"known": prof["stats"]["known"],
                    "marked": prof["stats"]["marked_known"] + prof["stats"]["marked_unknown"], "error": profile_error},
        "jobs": running, "recent": recent_videos(), "task_issue": task_issue,
        "log": applog.tail(60), "home": str(HOME), "extension_dir": str(extension_dir()),
    }


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):   # keep the console readable
        pass

    def local_only(self) -> bool:
        """Only answer requests addressed to this computer (blocks DNS-rebinding tricks)."""
        if LOCAL_HOST_RE.fullmatch(self.headers.get("Host", "")):
            return True
        self.send_error(403)
        return False

    def do_HEAD(self):
        self.send_error(405)

    def serve_static(self, path: str):
        """Only the page files are served: never the project folder (which holds .env with the keys)."""
        if path == "/data/index.json":
            bundled = video_catalog.read(BUNDLE_DATA) if (BUNDLE_DATA / "index.json").is_file() else []
            local = video_catalog.read(DATA)
            rows = {row["id"]: row for row in bundled + local}
            return self.send_json(list(rows.values()))
        if path in STATIC_PAGES:
            f, base = WEB / STATIC_PAGES[path], WEB
        elif path.startswith("/assets/"):
            f, base = WEB / path.lstrip("/"), WEB / "assets"
        elif re.fullmatch(r"/data/[A-Za-z0-9_.-]+\.json", path):
            name = path.split("/")[-1]
            f, base = (DATA / name, DATA) if (DATA / name).exists() else (BUNDLE_DATA / name, BUNDLE_DATA)
            if base == DATA and name != "index.json" and saved_result(f) is None:
                return self.send_json({"error": "已保存字幕不完整或已损坏，可以重新处理", "retry": True}, 409)
        else:
            return self.send_error(404)
        try:
            f = f.resolve()
            f.relative_to(base.resolve())
            body = f.read_bytes()
        except (OSError, ValueError):
            return self.send_error(404)
        ctype = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
                 ".json": "application/json; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
                 ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
                 ".ico": "image/x-icon"}.get(f.suffix.lower(), "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")   # always serve the latest player files
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        # YouTube requires a referring origin for embedded playback (player error 153).
        player_page = STATIC_PAGES.get(urlparse(self.path).path) == "player.html"
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin" if player_page else "no-referrer")
        super().end_headers()

    def send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        try:
            return self.get_request()
        except PS.ProfileCorrupt as error:
            return self.profile_problem(error)
        except TaskStoreCorrupt as error:
            return self.send_json({"error": str(error), "task_store": True}, 503)
        except (OSError, sqlite3.Error):
            return self.send_json({"error": "本地数据无法读取，请检查文件权限和磁盘状态"}, 503)

    def profile_problem(self, error):
        return self.send_json({"error": str(error), "recoverable": True, "fingerprint": error.fingerprint}, 503)

    def get_request(self):
        if not self.local_only():
            return
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path == "/api/health":
            self.note_extension()
            return self.send_json({"ok": True, "app": "adaptive-subs", "version": VERSION, "instance": INSTANCE_ID,
                                   "instance_locked": INSTANCE_GUARD is not None, "ext": 2, "asr": ASR.configured(), "provider": P.current(),
                                   "provider_name": P.PROVIDERS[P.current()]["name"], "model": P.model(),
                                   "key": P.has_key(), "setup": "/setup", "profile": "/profile"})
        if u.path.startswith("/api/") and not self.ext_ok():
            return
        if u.path == "/api/profile/export":
            return self.send_json(PROFILE.export_backup())
        if u.path == "/api/profile":
            if not self.ext_ok():
                return
            p = PROFILE.payload()
            if q.get("summary"):        # cheap check the extension polls: has anything changed?
                return self.send_json({k: p[k] for k in ("rev", "setup", "level", "prior", "test", "lists", "stats")})
            return self.send_json(p)
        if u.path == "/api/status":
            if not self.ext_ok():
                return
            return self.send_json(app_status(self.server.server_address[1]))
        if u.path == "/api/define":
            if not self.ext_ok():
                return
            return self.send_json(PS.define(q.get("w", "")[:40]) or {"word": q.get("w", "")[:40], "zh": ""})
        if u.path == "/api/settings":
            if not self.ext_ok():
                return
            return self.send_json({
                "provider": P.current(), "config_file": str(CONFIG),
                "providers": [{"id": k, "name": v["name"], "model": P.model(k), "default_model": v["model"], "note": v["note"], "price": v["price"],
                               "key_hint": key_hint(v["key"])} for k, v in P.PROVIDERS.items()],
                "asr": {"model": os.environ.get("ASR_MODEL", "mimo-v2.5-asr"), "key_hint": key_hint("MIMO_API_KEY")},
            })
        if u.path == "/api/open":
            return self.send_json({"error": "请更新播放器：开始处理需要 POST 请求"}, 405)
        if u.path == "/api/focus":
            return self.send_json({"error": "use POST"}, 405)
        if u.path == "/api/stream":
            job = get_job(q.get("v", ""), start=False)
            if not job:
                return self.send_json({"error": "no such job"}, 404)
            return self.stream(job)
        if u.path == "/api/ext/events":
            # long poll for the extension (its service worker has no EventSource)
            if not self.ext_ok():
                return
            job = get_job(q.get("v", ""), start=False)
            if not job:
                return self.send_json({"error": "no such job"}, 404)
            try:
                since = max(0, int(q.get("since", 0)))
            except ValueError:
                since = 0
            with job.cond:
                if since >= len(job.events) and job.state in ("running", "cancelling"):
                    job.cond.wait(timeout=8)
                events, nxt = job.events[since:], len(job.events)
            return self.send_json({"events": events, "next": nxt})
        return self.serve_static(u.path)

    def note_extension(self):
        global LAST_EXT
        if (self.headers.get("Origin") or "").startswith("chrome-extension://"):
            LAST_EXT = time.time()

    def ext_ok(self) -> bool:
        origin = self.headers.get("Origin")
        # A fixed custom header prevents ordinary cross-site requests, but it is
        # not an identity token. Explicit foreign/null origins are rejected too.
        valid_origin = not origin or origin == f"http://{self.headers.get('Host')}" or bool(
            re.fullmatch(r"chrome-extension://[a-p]{32}", origin))
        if self.headers.get(EXT_HEADER) == "1" and valid_origin:
            self.note_extension()
            return True
        self.send_json({"error": "forbidden"}, 403)
        return False

    def read_body(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            if not 0 < n <= 8_000_000 or self.headers.get_content_type() != "application/json":
                return None
            body = json.loads(self.rfile.read(n).decode("utf-8"),
                              parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
            json.dumps(body, ensure_ascii=False).encode("utf-8")  # Reject unpaired surrogate escapes before storage.
            return body if isinstance(body, dict) else None
        except (ValueError, RecursionError):
            return None

    def do_POST(self):
        try:
            return self.post_request()
        except PS.ProfileCorrupt as error:
            return self.profile_problem(error)
        except TaskStoreCorrupt as error:
            return self.send_json({"error": str(error), "task_store": True}, 503)
        except JobBusy as exc:
            return self.send_json({"error": str(exc), "retry": True}, 409)
        except SubtitleLimit as exc:
            return self.send_json({"error": str(exc)}, 400)
        except (OSError, sqlite3.Error):
            return self.send_json({"error": "本地文件保存失败，请检查磁盘空间和文件夹权限后重试"}, 507)

    def post_request(self):
        if not self.local_only():
            return
        path = urlparse(self.path).path
        if path not in ("/api/ext/open", "/api/settings", "/api/settings/test", "/api/profile", "/api/mark", "/api/explain",
                        "/api/open-folder", "/api/quit", "/api/open", "/api/focus", "/api/jobs/cancel", "/api/jobs/resume",
                        "/api/profile/import/preview", "/api/profile/import", "/api/profile/clear", "/api/profile/recover",
                        "/api/jobs/recover"):
            return self.send_json({"error": "not found"}, 404)
        if not self.ext_ok():
            return
        body = self.read_body()
        if body is None:
            return self.send_json({"error": "bad request"}, 400)
        if path == "/api/jobs/recover":
            if not isinstance(body.get("fingerprint"), str) or not re.fullmatch(r"[0-9a-f]{64}", body["fingerprint"]):
                return self.send_json({"error": "请刷新控制台后重新确认恢复"}, 400)
            with JOBS_LOCK:
                if any(j.state in ("running", "cancelling") or (j.thread and j.thread.is_alive()) for j in JOBS.values()):
                    raise JobBusy("请先等待任务完成，或取消任务并等待停止后再重建")
                try:
                    backup_path = TASK_STORE.recover(body["fingerprint"])
                except TaskStoreConflict as error:
                    return self.send_json({"error": str(error)}, 409)
            return self.send_json({"ok": True, "backup_path": backup_path})
        if path == "/api/profile/recover":
            if not isinstance(body.get("fingerprint"), str) or not re.fullmatch(r"[0-9a-f]{64}", body["fingerprint"]):
                return self.send_json({"error": "请重新打开画像页并确认恢复"}, 400)
            try:
                backup_path = PROFILE.recover(body["fingerprint"])
            except PS.ProfileConflict as error:
                return self.send_json({"error": str(error)}, 409)
            return self.send_json({"profile": PROFILE.payload(), "backup_path": backup_path})
        if path in ("/api/profile/import/preview", "/api/profile/import", "/api/profile/clear"):
            try:
                if path.endswith("/preview"):
                    p = PS.validate_backup(body.get("backup"))
                    return self.send_json({"known": len(p.get("known", [])), "unknown": len(p.get("unknown", [])),
                                           "test_date": (p.get("test") or {}).get("date"), "identity": PS.IDENTITIES.get(p.get("identity"), "未填写")})
                if path.endswith("/clear"):
                    PROFILE.clear_profile(body.get("expected_rev"))
                else:
                    PROFILE.import_backup(body.get("backup"), body.get("expected_rev"))
            except PS.ProfileConflict as exc:
                return self.send_json({"error": str(exc)}, 409)
            except ValueError as exc:
                return self.send_json({"error": str(exc)}, 400)
            return self.send_json(PROFILE.payload())
        if path in ("/api/jobs/cancel", "/api/jobs/resume"):
            vid = str(body.get("id", ""))
            if not (YT_ID_RE.fullmatch(vid) or EXT_ID_RE.fullmatch(vid)):
                return self.send_json({"error": "bad id"}, 400)
            if path.endswith("/cancel"):
                job = get_job(vid, start=False)
                if not job:
                    return self.send_json({"error": "任务不存在或已经停止"}, 404)
                cancel_job(job)
                return self.send_json({"id": vid, "state": job.state})
            saved = TASK_STORE.load(vid)
            if not saved:
                return self.send_json({"error": "没有找到已保存的进度，请重新打开视频"}, 404)
            if not P.has_key():
                return self.send_json({"error": key_missing_msg()}, 400)
            if saved["payload"].get("asr_url") and not ASR.configured():
                return self.send_json({"error": "请先在设置中填写语音识别 API key"}, 400)
            if saved["state"] == "done":
                return self.send_json({"id": vid, "state": "done"})
            job = get_job(vid, start=True)
            return self.send_json({"id": vid, "state": job.state})
        if path == "/api/open":
            vid = parse_video_id(body.get("url", "") if isinstance(body.get("url"), str) else "")
            if not vid:
                return self.send_json({"error": "没认出 YouTube 视频地址，请粘贴完整链接或 11 位视频 ID"}, 400)
            if saved_result(DATA / f"{vid}.json"):
                return self.send_json({"id": vid, "cached": True, "file": f"{vid}.json"})
            if not P.has_key():
                return self.send_json({"error": key_missing_msg()}, 400)
            get_job(vid, start=True)
            return self.send_json({"id": vid, "cached": False})
        if path == "/api/focus":
            try:
                focus = float(body.get("t", 0))
                if not math.isfinite(focus):
                    raise ValueError
            except (TypeError, ValueError, OverflowError):
                return self.send_json({"error": "invalid playback time"}, 400)
            job = get_job(str(body.get("v", "")), start=False)
            if job:
                with job.cond:
                    job.focus = max(0.0, focus)
            return self.send_json({"ok": True})
        if path == "/api/profile":
            err = PROFILE.update(body)
            return self.send_json({"error": err}, 400) if err else self.send_json(PROFILE.payload())
        if path == "/api/explain":
            return self.explain(body)
        if path == "/api/open-folder":
            folder = {"data": HOME / "data", "logs": applog.LOG_FILE.parent if applog.LOG_FILE else HOME,
                      "extension": extension_dir()}.get(str(body.get("what")))
            if not folder or not folder.exists():
                return self.send_json({"error": "文件夹不存在"}, 404)
            try:
                os.startfile(str(folder))          # Windows: open in File Explorer
            except Exception as e:
                return self.send_json({"error": short_err(e)}, 500)
            return self.send_json({"ok": True})
        if path == "/api/quit":
            print("  quit from the control panel")
            threading.Timer(0.3, lambda: os._exit(0)).start()
            return self.send_json({"ok": True})
        if path == "/api/mark":
            err = PROFILE.mark(body.get("key"), body.get("state"), body.get("detail"))
            return self.send_json({"error": err}, 400) if err else self.send_json({"ok": True, **PROFILE.payload()})
        if path == "/api/settings":
            return self.save_settings(body)
        if path == "/api/settings/test":
            return self.test_settings()
        vid = str(body.get("id", ""))
        youtube = body.get("source") == "youtube"
        # YouTube: the id is the video id; the server fetches the subtitles itself (the page can't: YouTube's
        # caption files only answer its own player) and keeps the result with the player's, so a video done
        # in one shows up done in the other. A video without English subtitles may have been recognised
        # before (speech recognition result, kept with the extension's).
        if not (YT_ID_RE if youtube else EXT_ID_RE).fullmatch(vid):
            return self.send_json({"error": "bad id"}, 400)
        saved = (DATA if youtube else EXT_DATA) / f"{vid}.json"
        if youtube and saved_result(saved) is None and saved_result(EXT_DATA / f"yt_{vid}_asr.json") is not None:
            vid = f"yt_{vid}_asr"
            saved = EXT_DATA / f"{vid}.json"
        data = saved_result(saved)
        if data is not None:       # Partial/corrupt results must remain retryable.
            tags = tag_only_lines(data)
            if tags:                 # "<chinese>" for cheering / music (made before tags were filtered): no speech there
                for i in tags:
                    data["cues"][i].update(text="", notes=[], zh="")
                write_json(saved, data)
                print(f"[{vid}] removed {len(tags)} non-speech labels (<chinese> ...) from the saved subtitles")
            return self.send_json({"id": vid, "cached": True, "data": data})
        if youtube and body.get("subs_only"):
            # the extension shows the plain English first; glosses are generated only after a click
            # (then the subtitles fetched here are used, not fetched again). No API key needed for this.
            job = get_job(vid, start=False)
            if job and job.state == "running":
                return self.send_json({"id": vid, "cached": False, "running": True})
            return self.youtube_subs(vid)
        if not P.has_key():
            return self.send_json({"error": key_missing_msg()}, 400)
        try:
            duration = float(body.get("duration") or 0)
            if not math.isfinite(duration) or duration < 0:
                raise ValueError
        except (TypeError, ValueError, OverflowError):
            return self.send_json({"error": "视频时长必须是有限的非负数"}, 400)
        title = str(body.get("title") or vid)[:200]
        if body.get("probe"):       # the extension asks: done before / still running? (starts nothing)
            job = get_job(vid, start=False)
            if job and job.state == "running":
                return self.send_json({"id": vid, "cached": False, "running": True})
            if body.get("source") == "asr" and not ASR.configured():
                return self.send_json({"error": "noasr"})
            return self.send_json({"id": vid, "cached": False})
        if youtube:
            with YT_SUBS_LOCK:
                sub = YT_SUBS.pop(vid, None)
            get_job(vid, start=True, sub=sub)
            return self.send_json({"id": vid, "cached": False})
        if body.get("source") == "asr":
            # no subtitles: recognise the speech. The server builds the URL itself, from a Bilibili BV id or a
            # YouTube video id only (never a URL from the page).
            bvid, yt = str(body.get("bvid", "")), str(body.get("yt", ""))
            if re.fullmatch(r"BV[0-9A-Za-z]{10}", bvid):
                try:
                    page = min(max(int(body.get("p") or 1), 1), 1000)
                except (TypeError, ValueError, OverflowError):
                    page = 1
                url = f"https://www.bilibili.com/video/{bvid}/?p={page}"
            elif YT_ID_RE.fullmatch(yt) and vid == f"yt_{yt}_asr":
                url = f"https://www.youtube.com/watch?v={yt}"
            else:
                return self.send_json({"error": "bad video"}, 400)
            if not ASR.configured():
                return self.send_json({"error": "还没有填写小米 MiMo 的 API key（语音识别用）：在插件弹窗里点「程序设置」填写"}, 400)
            get_job(vid, start=True, sub={"title": title, "duration": duration, "source": "asr", "cues": []},
                    save_dir=EXT_DATA, asr_url=url)
            return self.send_json({"id": vid, "cached": False})
        cues = clean_ext_cues(body.get("cues"))
        if not cues:
            return self.send_json({"error": "字幕是空的"}, 400)
        sub = {"title": title, "duration": duration,
               "source": "auto" if body.get("source") == "auto" else "manual", "cues": cues}
        get_job(vid, start=True, sub=sub, save_dir=EXT_DATA)
        return self.send_json({"id": vid, "cached": False})

    def youtube_subs(self, vid: str):
        """English subtitles of a YouTube video for the extension, without generating glosses."""
        try:
            sub = fetch_subtitles(vid)
        except VideoAccessError as error:
            return self.send_json({"id": vid, "error": str(error), "retry": error.retry, "code": error.code})
        except NoSubtitles:
            return self.send_json({"id": vid, "nosubs": True, "error": "这个视频没有英文字幕"})
        except FetchFailed as e:
            print(f"[{vid}] 连不上 YouTube（{e}一直超时）")
            return self.send_json({"id": vid, "retry": True,
                                   "error": f"连不上 YouTube：{e}超时，已自动重试 {NET_TRIES} 次。可能是网络或代理不稳定，请点「重试」。"})
        except Exception as e:
            if e.__class__.__name__ == "DownloadError":      # private / removed / region-locked ...
                return self.send_json({"id": vid, "error": f"YouTube 拒绝了这个视频：{short_err(e)}"})
            traceback.print_exc()
            return self.send_json({"id": vid, "error": f"获取字幕失败：{short_err(e)}"})
        with YT_SUBS_LOCK:
            YT_SUBS[vid] = sub
            while len(YT_SUBS) > 30:                       # videos opened but never glossed
                YT_SUBS.pop(next(iter(YT_SUBS)))
        print(f"[{vid}] {len(sub['cues'])} lines of English subtitles ({sub['source']}); glosses when asked: {sub['title']}")
        return self.send_json({"id": vid, "cached": False, "cues": sub["cues"], "source": sub["source"]})

    def explain(self, body: dict):
        """Meaning of a clicked word in its sentence (words the annotations didn't cover).
        The dictionary only has general senses ("ligature" = 绳); this asks the annotation model what the
        word means here. Cached on disk by call_json, so the same word in the same line costs nothing again."""
        word, sentence = body.get("word"), body.get("sentence")
        if (not isinstance(word, str) or not isinstance(sentence, str) or len(sentence) > MAX_CUE_CHARS
                or not re.fullmatch(r"[A-Za-z][A-Za-z'’-]{0,39}", word)
                or not re.search(r"(?<!\w)" + re.escape(word) + r"(?!\w)", sentence, re.IGNORECASE)):
            return self.send_json({"error": "请选择原句中的英文单词，原句不能超过 2000 个字符"}, 400)
        sentence = A.clean(sentence)
        if not P.has_key():
            return self.send_json({"error": key_missing_msg()}, 400)
        try:
            with CONFIG_LOCK:
                service, model = client(), P.model()
            data = A.call_json(service, model, "none", EXPLAIN_PROMPT, {"word": word, "sentence": sentence})
        except Exception as e:
            return self.send_json({"error": f"查询失败：{short_err(e)[:80]}"}, 502)
        return self.send_json(data)

    def save_settings(self, body: dict):
        updates = {}
        prov = str(body.get("provider", ""))
        if prov:
            if prov not in P.PROVIDERS:
                return self.send_json({"error": "未知的注释模型"}, 400)
            updates["ANNOTATION_PROVIDER"] = prov
        models = body.get("models", {})
        if not isinstance(models, dict):
            return self.send_json({"error": "模型设置格式不正确"}, 400)
        for pid, value in {**models, **({"asr": body["asr_model"]} if "asr_model" in body else {})}.items():
            if pid not in (*P.PROVIDERS, "asr") or not isinstance(value, str) or (value and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}", value)):
                return self.send_json({"error": "模型名称格式不正确"}, 400)
            updates[f"{pid.upper()}_MODEL"] = value
        keys = body.get("keys") if isinstance(body.get("keys"), dict) else {}
        for pid, val in keys.items():
            if pid not in P.PROVIDERS or not isinstance(val, str):
                continue
            val = val.strip()
            if val == "":
                continue                       # empty field: keep the saved key
            if val == "-":
                updates[P.PROVIDERS[pid]["key"]] = ""          # "-": remove
            elif re.fullmatch(r"[A-Za-z0-9_\-.]{10,200}", val):
                updates[P.PROVIDERS[pid]["key"]] = val
            else:
                return self.send_json({"error": f"{P.PROVIDERS[pid]['name']} 的 key 格式不对（不能有空格或中文）"}, 400)
        save_config(updates)
        print(f"  settings saved: annotation model = {P.model()}, keys: "
              + ", ".join(f"{k}={'yes' if P.has_key(k) else 'no'}" for k in P.PROVIDERS))
        return self.send_json({"ok": True, "provider": P.current()})

    def test_settings(self):
        """Check selected services and model names without generating content."""
        out, available = {}, {}
        with CONFIG_LOCK:
            selected = P.current()
            checks = {pid: (P.client(pid) if P.has_key(pid) else None, P.model(pid)) for pid in {selected, "mimo"}}
            asr_model = os.environ.get("ASR_MODEL", "mimo-v2.5-asr")
        for pid, (service, model) in checks.items():
            if service is None:
                out[pid] = "未填写"
                if pid == "mimo":
                    out["asr"] = "未填写"
                continue
            try:
                response = service.with_options(timeout=15, max_retries=0).models.list()
                ids = {m.id for m in response.data}
                available[pid] = sorted(ids)[:200]
                out[pid] = "ok" if model in ids else f"连接正常，但模型列表中没有 {model}，请核对模型名或访问权限"
                if pid == "mimo":
                    out["asr"] = "ok" if asr_model in ids else f"连接正常，但模型列表中没有 {asr_model}，请核对语音模型名或访问权限"
            except Exception as e:
                code = getattr(e, "status_code", None)
                out[pid] = "key 无效或权限不足" if code in (401, 403) else f"连接失败：{short_err(e)[:80]}"
                if pid == "mimo":
                    out["asr"] = out[pid]
        return self.send_json({"results": out, "models": available})

    def stream(self, job: Job):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.end_headers()
        i = 0
        try:
            while True:
                with job.cond:
                    if i >= len(job.events):
                        job.cond.wait(timeout=15)
                    batch = job.events[i:]
                    i = len(job.events)
                if not batch:
                    self.wfile.write(b": ping\n\n")     # keep-alive
                    self.wfile.flush()
                    continue
                for typ, data in batch:
                    self.wfile.write(f"event: {typ}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8"))
                self.wfile.flush()
                if any(t in ("done", "fail") for t, _ in batch):
                    return
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            return


def port_number(value):
    try:
        port = int(value)
        if 1 <= port <= 65535:
            return port
    except (ValueError, TypeError):
        pass
    raise argparse.ArgumentTypeError("端口须为 1–65535 的整数")


def main():
    global INSTANCE_GUARD
    HOME.mkdir(parents=True, exist_ok=True)
    A.load_dotenv(CONFIG)
    A.CACHE_DIR = HOME / ".cache"
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=port_number, default=os.environ.get("PORT", "8765"))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--open", action="store_true", help="open the player in the browser once the server is up")
    ap.add_argument("--app", action="store_true", help="open the control panel in the browser")
    ap.add_argument("--gui", action="store_true", help="show the control panel in a window (no console needed)")
    args = ap.parse_args()
    if args.host not in ("127.0.0.1", "localhost"):
        sys.exit("Adaptive Subs only listens on this computer (127.0.0.1): it holds your API keys.")
    if applog.LOG_FILE is None:
        applog.install(HOME / "logs")
    want_browser = (args.open or args.app) and not args.gui and not os.environ.get("ASUBS_NO_BROWSER")

    def open_browser(port):
        if not want_browser:
            return
        page = "player.html" if args.open else "app"
        if page:
            import webbrowser
            threading.Timer(1.0, webbrowser.open, [f"http://{args.host}:{port}/{page}"]).start()

    guard = InstanceLock(HOME)
    if not guard.acquire():
        return reuse_instance(args, open_browser, want_browser)
    try:
        INSTANCE_GUARD = guard
        guard.publish(VERSION, INSTANCE_ID, args.host)
        return serve_owned(args, open_browser, guard)
    finally:
        INSTANCE_GUARD = None
        guard.close()


def reuse_instance(args, open_browser, want_browser):
    # A stale metadata file alone never blocks startup: only a live OS lock reaches this branch.
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        info = read_instance(HOME)
        existing = probe_server(info["host"], info["port"]) if info else None
        if existing and existing.get("instance") == info["instance"]:
            if existing["version"] != VERSION:
                raise RuntimeError(f"同一数据目录正在由 Adaptive Subs {existing['version']} 使用；当前版本是 {VERSION}。"
                                   "请先退出已运行的程序，再启动当前版本。")
            args.host = info["host"]
            print(f"Adaptive Subs is already running: http://{info['host']}:{info['port']}/app")
            if args.gui:
                return run_window(f"http://{info['host']}:{info['port']}/app", None)
            open_browser(info["port"])
            if want_browser:
                time.sleep(2)
            return
        time.sleep(0.1)
    raise RuntimeError("同一数据目录的程序正在启动或退出，尚未确认连接。请稍后重试；不要删除正在使用的锁文件。")


def serve_owned(args, open_browser, guard):
    # The directory lock excludes another modern owner of this data. Other directories
    # can use the next port; legacy processes must be closed before upgrading.
    port, httpd = args.port, None
    last_port = min(args.port + 19, 65535)
    for candidate in range(args.port, last_port + 1):
        try:
            httpd = Server((args.host, candidate), Handler)
            port = candidate
            break
        except OSError:
            existing = probe_server(args.host, candidate)
            if existing and existing.get("instance_locked") is not True:
                if existing["version"] != VERSION:
                    raise RuntimeError(f"端口 {candidate} 上运行着 Adaptive Subs {existing['version']}，当前启动的是 {VERSION}。"
                                       "请先退出已运行的程序，再启动当前版本，避免继续使用旧版或同时写入数据。")
                raise RuntimeError("已运行的程序未启用同目录保护，请先退出，再启动当前版本。")
            print(f"  port {candidate} is in use by another program, trying {candidate + 1}…")
    if httpd is None:
        sys.exit(f"No free port between {args.port} and {last_port}.")

    try:
        guard.publish(VERSION, INSTANCE_ID, args.host, port)
        return run_server(httpd, args, port, open_browser)
    finally:
        httpd.server_close()


def run_server(httpd, args, port, open_browser):
    try:
        TASK_STORE.mark_interrupted()
    except (TaskStoreCorrupt, OSError, sqlite3.Error) as error:
        print(f"任务记录暂不可用，控制台仍可打开：{short_err(error)}")

    # warm up spaCy + the frequency list in the background, so the first video starts faster
    def warm():
        with NLP_LOCK:
            A.nlp()
        A.load_ranks()
    threading.Thread(target=warm, daemon=True).start()

    key = "yes" if P.has_key() else "NO"
    asr = "yes" if ASR.configured() else "NO"
    print(f"\n  Adaptive Subs: http://{args.host}:{port}/player.html")
    print(f"  annotation model: {P.PROVIDERS[P.current()]['name']} ({P.model()}), key: {key} | speech recognition key: {asr}")
    print(f"  settings: http://{args.host}:{port}/setup   (saved in {CONFIG})\n")
    open_browser(port)
    if args.gui and os.environ.get("ASUBS_HEADLESS"):
        args.gui = False             # tests / automation: serve only, no window
    if args.gui:
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        # always the control panel (its intro plays); without a key its stage leads to the settings
        return run_window(f"http://{args.host}:{port}/app", httpd)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


QUIT_TEXT = ("关闭窗口会停止 Adaptive Subs，B 站插件就不能生成注释了。\n"
             "看视频时可以把窗口最小化。确定要退出吗？")


def run_window(url: str, httpd):
    """The program's window: the control panel in a native window (Edge WebView2 via pywebview).
    Closing it stops the program. Without WebView2, the panel opens in the browser instead."""
    try:
        import webview
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True     # target=_blank links -> the browser
        w, h = window_size()
        webview.create_window("Adaptive Subs", url, width=w, height=h, min_size=(min(w, 760), min(h, 520)),
                              confirm_close=True, background_color="#04060e")      # = the intro: no white flash on launch
        webview.start(localization={"global.quitConfirmation": QUIT_TEXT, "global.ok": "退出", "global.cancel": "取消"},
                      private_mode=False, storage_path=str(HOME / "webview"))
    except Exception as e:
        print(f"  could not open the window ({short_err(e)}); opening the control panel in the browser")
        import webbrowser
        webbrowser.open(url.replace("setup?app=1", "setup").replace("/app", "/app?browser=1"))
        if httpd is None:
            return
        while True:                     # keep serving; "退出程序" in the panel stops it
            time.sleep(3600)
    os._exit(0)


def window_size() -> tuple[int, int]:
    """1040 x 760, or smaller to fit the screen's work area (above the taskbar), in logical pixels."""
    try:
        import ctypes
        from ctypes import wintypes
        r = wintypes.RECT()
        ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(r), 0)     # SPI_GETWORKAREA
        scale = max(1.0, ctypes.windll.user32.GetDpiForSystem() / 96)
        return int(min(1040, (r.right - r.left) / scale - 40)), int(min(760, (r.bottom - r.top) / scale - 40))
    except Exception:
        return 1040, 720


def dark_mode() -> bool:
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except Exception:
        return False


def valid_health(value):
    """Identify the product, including its legacy health shape; this is not authentication."""
    return (isinstance(value, dict) and value.get("ok") is True
            and value.get("app", "adaptive-subs") == "adaptive-subs"
            and isinstance(value.get("version"), str)
            and re.fullmatch(r"[0-9]+\.[0-9]+(?:\.[0-9]+)?(?:[-+][0-9A-Za-z.-]+)?", value["version"]) is not None
            and len(value["version"]) <= 80 and type(value.get("ext")) is int and value["ext"] >= 1
            and value.get("setup") == "/setup" and value.get("profile") == "/profile")


def probe_server(host: str, port: int):
    import urllib.request
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(f"http://{host}:{port}/api/health", timeout=1.5) as r:
            raw = r.read(16385)
            if r.status != 200 or len(raw) > 16384:
                return None
            value = json.loads(raw)
            return value if valid_health(value) else None
    except Exception:
        return None


def is_live_server(host: str, port: int) -> bool:
    return probe_server(host, port) is not None


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
