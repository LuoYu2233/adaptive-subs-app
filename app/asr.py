"""Speech recognition for videos without English subtitles (Bilibili via the browser extension).

The ASR API (Xiaomi MiMo, OpenAI-compatible chat endpoint) returns plain text without timestamps,
so the timing comes from us: the audio is cut at pauses into short pieces (about 1-7 s, one
subtitle line each) and every piece is recognised on its own. That gives each line exact
start/end times, and lets the pieces near the viewer's position be recognised first.

    download_audio(url)  -> audio file (yt-dlp)
    decode(path)         -> 16 kHz mono int16 samples (ffmpeg)
    segment(pcm)         -> [(start, end)] in seconds
    transcribe(pcm, s, e) -> text ("" for silence / music)
"""

from __future__ import annotations

import base64
import bisect
import io
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import wave
from pathlib import Path
from urllib.parse import urlparse

import numpy as np
import annotate as A
from storage import write_json
from usage_accounting import audio_seconds, audio_totals

SR = 16000
FRAME = 0.03                 # analysis frame, seconds
MAX_LINE = 7.0               # longest piece (one subtitle line)
MIN_PAUSE = 0.3              # a pause at least this long ends a line
MAX_HOURS = 3                # refuse longer videos (memory: ~115 MB of samples per hour)
PRICE_PER_HOUR_CNY = 0.5     # MiMo V2.5 ASR list price


class AudioError(Exception):
    pass


# ------------------------------------------------------------- audio ---
#
# Bilibili's CDN sends the first ~3 MB of every connection at full speed, then throttles that connection
# to ~100-250 KiB/s (measured from overseas and typical in mainland too). So the audio is fetched in
# 1 MB ranges over several connections at once: each range fits in a connection's fast start. Ranges are
# taken in order (earliest missing first) and kept in memory, so decoding and recognition can start on
# the beginning of the file while the rest is still arriving. Two hours of audio (~80 MB) takes ~20-60 s
# instead of 10+ minutes.

CHUNK = 1 << 20              # bytes per range request: stays inside the CDN's fast start
FIRST_CHUNKS = 4             # the first ranges are smaller (256 KB ≈ 20 s of audio each): sound sooner
DL_WORKERS = 8               # parallel connections
CHUNK_TRIES = 4
MAX_AUDIO_BYTES = 384 * 1024 * 1024  # compressed audio; prevents unbounded allocation from a bad header


def audio_formats(url: str) -> tuple[dict, list[dict]]:
    """yt-dlp metadata and the audio-only streams, best first."""
    import yt_dlp

    opts = {"quiet": True, "no_warnings": True, "socket_timeout": 15, "skip_download": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if (info.get("duration") or 0) > MAX_HOURS * 3600:
        raise AudioError(f"视频超过 {MAX_HOURS} 小时，暂不支持语音识别")
    audio = [f for f in info.get("formats") or [] if f.get("vcodec") == "none" and f.get("acodec") not in (None, "none")
             and f.get("url") and str(f.get("protocol", "https")).startswith("http")]
    # plain AAC around 64-140 kbps first (small, and the low-bitrate HE-AAC stream fails most often)
    audio.sort(key=lambda f: (f.get("acodec", "").startswith("mp4a.40.5"), abs((f.get("abr") or 100) - 90)))
    return info, audio


class ChunkedDownload:
    """Downloads one URL into memory with parallel 1 MB range requests, earliest missing range first.
    read_at() blocks until the requested bytes have arrived (and pulls that range to the front of the
    queue), so a decoder can read the file while it downloads."""

    def __init__(self, url: str, headers: dict, workers: int = DL_WORKERS, chunk: int = CHUNK):
        if workers < 1 or chunk < 4:
            raise ValueError("Invalid audio download concurrency or chunk size")
        self.url, self.headers, self.chunk = url, dict(headers or {}), chunk
        self.cond = threading.Condition()
        self.error: str | None = None
        self.cancelled = False
        self.size = self._probe()
        self.buf = bytearray(self.size)
        # range boundaries: a few small ones first (a cold connection is slow to start), then 1 MB each
        self.starts, pos = [], 0
        while pos < self.size:
            self.starts.append(pos)
            pos += chunk // 4 if len(self.starts) <= FIRST_CHUNKS else chunk
        n = len(self.starts)
        self.done = [False] * n
        self.busy = [False] * n
        self.want = 0                   # chunk the reader is waiting for
        self.received = 0
        self.started = time.time()
        if self.ranged:
            self.threads = [threading.Thread(target=self._worker, daemon=True) for _ in range(min(workers, n))]
        else:                           # no range support: one plain stream (still readable while it arrives)
            self.threads = [threading.Thread(target=self._whole, daemon=True)]
        for t in self.threads:
            t.start()

    def _open(self, start: int | None = None, end: int | None = None, timeout: float = 15):
        import urllib.request
        h = dict(self.headers)
        h["Accept-Encoding"] = "identity"  # Range offsets describe the audio file bytes.
        if start is not None:
            h["Range"] = f"bytes={start}-{end}"
        return urllib.request.urlopen(urllib.request.Request(self.url, headers=h), timeout=timeout)

    def _probe(self) -> int:
        with self._open(0, 0) as r:
            cr = r.headers.get("Content-Range", "")
            m = re.fullmatch(r"bytes 0-0/(\d+)", cr)
            if r.status == 206:
                if not m:
                    raise AudioError("音频服务器返回了无效的分段范围")
                self.ranged = True
                size_text = m.group(1)
            elif r.status == 200:
                self.ranged = False
                size_text = r.headers.get("Content-Length") or "0"
            else:
                raise AudioError("音频服务器未返回可读取的文件")
        try:
            size = int(size_text)
        except (ValueError, TypeError):
            raise AudioError("音频服务器没有提供有效文件大小") from None
        if size <= 0:
            raise AudioError("音频服务器没有提供有效文件大小")
        if size > MAX_AUDIO_BYTES:
            raise AudioError(f"音频文件超过 {MAX_AUDIO_BYTES // 1024 // 1024} MB，请选择较短或较小的视频")
        return size

    def _next(self) -> int | None:
        with self.cond:
            n = len(self.done)
            for i in list(range(self.want, n)) + list(range(0, self.want)):
                if not self.done[i] and not self.busy[i]:
                    self.busy[i] = True
                    return i
            return None

    def _worker(self):
        while not self.cancelled and self.error is None:
            i = self._next()
            if i is None:
                return
            s = self.starts[i]
            e = (self.starts[i + 1] if i + 1 < len(self.starts) else self.size) - 1
            for attempt in range(CHUNK_TRIES):
                try:
                    with self._open(s, e) as r:
                        if r.status != 206 or r.headers.get("Content-Range") != f"bytes {s}-{e}/{self.size}":
                            raise AudioError("音频服务器返回的分段范围与请求不符")
                        data = r.read(e - s + 2)  # At most the requested bytes plus one overflow sentinel.
                    if len(data) != e - s + 1:
                        raise IOError(f"short read {len(data)}/{e - s + 1}")
                    with self.cond:
                        self.buf[s:e + 1] = data
                        self.done[i] = True
                        self.busy[i] = False
                        self.received += len(data)
                        self.cond.notify_all()
                    break
                except Exception as ex:      # timeouts, 503 from a busy mirror ...: try again
                    if self.cancelled:
                        return
                    if attempt == CHUNK_TRIES - 1:
                        with self.cond:
                            self.error = f"音频下载失败：{str(ex).splitlines()[0][:100]}"
                            self.cond.notify_all()
                        return
                    time.sleep(0.5 * (attempt + 1))

    def _whole(self):
        try:
            with self._open(timeout=30) as r:
                pos = 0
                while not self.cancelled:
                    data = r.read(min(65536, self.size - pos + 1))
                    if not data:
                        if pos != self.size:
                            raise AudioError(f"音频下载提前结束（已收到 {pos}/{self.size} 字节），请重试")
                        with self.cond:
                            self.done[-1] = True  # Final chunk is complete only after a verified EOF.
                            self.cond.notify_all()
                        break
                    if pos + len(data) > self.size:
                        raise AudioError("音频文件大小在下载期间变化，请重试")
                    with self.cond:
                        self.buf[pos:pos + len(data)] = data
                        pos += len(data)
                        self.received = pos
                        for i, st in enumerate(self.starts):
                            if self._end(i) <= pos and self._end(i) < self.size:
                                self.done[i] = True
                        self.cond.notify_all()
        except Exception as ex:
            with self.cond:
                self.error = f"音频下载失败：{str(ex).splitlines()[0][:100]}"
                self.cond.notify_all()

    def _end(self, i: int) -> int:
        return self.starts[i + 1] if i + 1 < len(self.starts) else self.size

    def read_at(self, pos: int, n: int) -> bytes:
        """Up to n bytes from pos; waits for them to arrive. b"" only at the end of the file."""
        if pos < 0:
            raise ValueError("Audio read position cannot be negative")
        if pos >= self.size or n <= 0:
            return b""
        i = bisect.bisect_right(self.starts, pos) - 1
        with self.cond:
            while not self.done[i]:
                if self.error:
                    raise AudioError(self.error)
                if self.cancelled:
                    raise AudioError("已取消")
                self.want = i
                self.cond.wait(timeout=1.0)
            end = min(pos + n, self.size)
            j = i
            while self._end(j) < end and self.done[j + 1]:            # contiguous arrived bytes only
                j += 1
            end = min(end, self._end(j))
            return bytes(self.buf[pos:end])

    def progress(self) -> float:
        return self.received / self.size if self.size else 0.0

    def cancel(self):
        with self.cond:
            self.cancelled = True
            self.cond.notify_all()


class _Reader(io.RawIOBase):
    """File-like view of a ChunkedDownload for PyAV (blocking reads, seeks allowed)."""

    def __init__(self, dl: ChunkedDownload):
        self.dl, self.pos = dl, 0

    def readable(self):
        return True

    def seekable(self):
        return True

    def read(self, n=-1):
        if n is None or n < 0:
            n = self.dl.size - self.pos
        data = self.dl.read_at(self.pos, n)
        self.pos += len(data)
        return data

    def readinto(self, b):
        data = self.read(len(b))
        b[:len(data)] = data
        return len(data)

    def seek(self, off, whence=io.SEEK_SET):
        if whence not in (io.SEEK_SET, io.SEEK_CUR, io.SEEK_END):
            raise ValueError("Invalid audio seek mode")
        target = off if whence == io.SEEK_SET else self.pos + off if whence == io.SEEK_CUR else self.dl.size + off
        if target < 0:
            raise ValueError("Audio seek position cannot be negative")
        self.pos = target
        return self.pos

    def tell(self):
        return self.pos


def stream_audio(url: str, on_status=None, cancelled=lambda: False):
    """Yields 16 kHz mono int16 sample blocks while the audio downloads.
    The first item is the stream's duration in seconds (float) — for sizing buffers and progress.
    Tries the next audio stream if one fails before producing any sound."""
    try:
        import av
    except ImportError:
        av = None
    info, formats = audio_formats(url)
    yield float(info.get("duration") or 0)
    if av is None:                               # no PyAV: download everything, decode with ffmpeg
        path = download_audio(url)
        try:
            yield decode(path)
        finally:
            shutil.rmtree(path.parent, ignore_errors=True)
        return
    last = None
    for f in formats or []:
        dl = None
        produced = False
        try:
            dl = ChunkedDownload(f["url"], f.get("http_headers") or {})
            host = urlparse(f["url"]).hostname
            print(f"  audio stream {f.get('format_id')}: {dl.size / 1e6:.1f} MB, "
                  f"{'parallel ranges' if dl.ranged else 'single stream'} from {host}")
            stop = threading.Event()

            def report():                         # download progress for the status line
                while not stop.wait(1.0):
                    if on_status:
                        on_status(dl.progress(), dl.received / max(0.1, time.time() - dl.started))
            threading.Thread(target=report, daemon=True).start()
            try:
                with av.open(_Reader(dl), mode="r") as container:
                    stream = next(s for s in container.streams if s.type == "audio")
                    resampler = av.AudioResampler(format="s16", layout="mono", rate=SR)
                    for frame in container.decode(stream):
                        if cancelled():
                            return
                        for r in resampler.resample(frame):
                            produced = True
                            yield r.to_ndarray().reshape(-1).astype(np.int16, copy=False)
                    for r in resampler.resample(None):
                        yield r.to_ndarray().reshape(-1).astype(np.int16, copy=False)
            finally:
                stop.set()
            took = time.time() - dl.started
            print(f"  audio downloaded: {dl.size / 1e6:.1f} MB in {took:.1f}s ({dl.size / 1024 / max(0.1, took):.0f} KiB/s)")
            return
        except AudioError as e:
            last = e
            if produced:
                raise
        except Exception as e:                   # this stream failed: try the next one
            if produced:
                raise AudioError(f"音频解码失败：{str(e)[:120]}")
            last = e
        finally:
            if dl:
                dl.cancel()
    raise AudioError(str(last) if isinstance(last, AudioError) else
                     f"音频下载失败：{str(last).splitlines()[0][:120] if last else '没有音频流'}")


def download_audio(url: str) -> Path:
    """Download the audio track into a temp folder. Bilibili offers a few audio streams and some of
    its mirrors answer 503 now and then, so each stream is tried in turn."""
    import yt_dlp

    tmp = Path(tempfile.mkdtemp(prefix="asubs-audio-"))
    base = {"quiet": True, "no_warnings": True, "socket_timeout": 15, "retries": 2, "fragment_retries": 2,
            "outtmpl": str(tmp / "audio.%(ext)s")}
    with yt_dlp.YoutubeDL({**base, "skip_download": True}) as ydl:
        info = ydl.extract_info(url, download=False)
    if (info.get("duration") or 0) > MAX_HOURS * 3600:
        raise AudioError(f"视频超过 {MAX_HOURS} 小时，暂不支持语音识别")
    audio = [f for f in info.get("formats") or [] if f.get("vcodec") == "none" and f.get("acodec") not in (None, "none")]
    # plain AAC around 64-140 kbps first (small, and the low-bitrate HE-AAC stream fails most often)
    audio.sort(key=lambda f: (f.get("acodec", "").startswith("mp4a.40.5"), abs((f.get("abr") or 100) - 90)))
    last = None
    for f in audio or [{"format_id": "bestaudio"}]:
        try:
            with yt_dlp.YoutubeDL({**base, "format": f["format_id"]}) as ydl:
                ydl.download([url])
            got = next(tmp.glob("audio.*"), None)
            if got and got.stat().st_size > 0:
                return got
        except Exception as e:      # this stream failed: try the next one
            last = e
            for p in tmp.glob("audio.*"):
                p.unlink(missing_ok=True)
    shutil.rmtree(tmp, ignore_errors=True)
    raise AudioError(f"音频下载失败：{str(last).splitlines()[0][:120] if last else '没有音频流'}")


def decode(path: Path) -> np.ndarray:
    """Any audio file -> 16 kHz mono int16 samples. PyAV (bundled ffmpeg libraries) if installed,
    else the ffmpeg program."""
    try:
        import av
    except ImportError:
        av = None
    if av is not None:
        try:
            chunks = []
            with av.open(str(path)) as container:
                stream = next(s for s in container.streams if s.type == "audio")
                resampler = av.AudioResampler(format="s16", layout="mono", rate=SR)
                for frame in container.decode(stream):
                    for f in resampler.resample(frame):
                        chunks.append(f.to_ndarray().reshape(-1))
                for f in resampler.resample(None):
                    chunks.append(f.to_ndarray().reshape(-1))
            if chunks:
                return np.concatenate(chunks).astype(np.int16, copy=False)
        except Exception as e:
            raise AudioError(f"音频解码失败：{str(e)[:120]}")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise AudioError("没有找到 ffmpeg：语音识别需要它（winget install ffmpeg）")
    p = subprocess.run([ffmpeg, "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"],
                       capture_output=True, timeout=600)
    if p.returncode != 0 or not p.stdout:
        raise AudioError("音频解码失败：" + p.stderr.decode("utf-8", "replace")[:120])
    return np.frombuffer(p.stdout, dtype=np.int16)


# ------------------------------------------------------- segmentation ---

def _runs(mask: np.ndarray) -> list[list[int]]:
    """[start, end) frame ranges where mask is True."""
    d = np.diff(np.concatenate([[0], mask.astype(np.int8), [0]]))
    return [[int(a), int(b)] for a, b in zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1))]


def segment(pcm: np.ndarray) -> list[tuple[float, float]]:
    """Cut speech into subtitle-sized pieces at pauses, by loudness (no model needed).
    Threshold adapts to each video: a bit above its quiet level, relative to its loud level."""
    hop = int(SR * FRAME)
    n = len(pcm) // hop
    if n == 0:
        return []
    x = pcm[: n * hop].astype(np.float32).reshape(n, hop)
    db = 20 * np.log10(np.sqrt((x * x).mean(axis=1)) + 1e-3)
    floor, loud = np.percentile(db, 10), np.percentile(db, 95)
    speech = db > floor + max(6.0, 0.3 * (loud - floor))

    # fill gaps shorter than 0.2 s, drop blips shorter than 0.15 s
    for a, b in _runs(~speech):
        if 0 < a and b < n and (b - a) * FRAME < 0.2:
            speech[a:b] = True
    for a, b in _runs(speech):
        if (b - a) * FRAME < 0.15:
            speech[a:b] = False

    utts: list[list[int]] = []
    for a, b in _runs(speech):                       # join words into lines across short pauses
        if utts and (a - utts[-1][1]) * FRAME < MIN_PAUSE:
            utts[-1][1] = b
        else:
            utts.append([a, b])

    pieces: list[tuple[int, int]] = []
    w = max(1, int(0.15 / FRAME))
    smooth = np.convolve(db, np.ones(w) / w, mode="same")

    def split(a: int, b: int):                       # long line: cut at its quietest moment
        pending = [(a, b)]
        while pending:
            lo_piece, hi_piece = pending.pop()
            if (hi_piece - lo_piece) * FRAME <= MAX_LINE:
                pieces.append((lo_piece, hi_piece))
                continue
            lo, hi = lo_piece + int(1.5 / FRAME), hi_piece - int(1.5 / FRAME)
            k = lo + int(np.argmin(smooth[lo:hi]))
            pending.extend(((k, hi_piece), (lo_piece, k)))

    for a, b in utts:
        split(a, b)

    # very short pieces ("In my life.") join the closer neighbour, if the pause between is small
    merged = [list(x) for x in pieces]
    i = 0
    while i < len(merged):
        a, b = merged[i]
        if (b - a) * FRAME < 1.2 and len(merged) > 1:
            gap_prev = (a - merged[i - 1][1]) * FRAME if i > 0 else 1e9
            gap_next = (merged[i + 1][0] - b) * FRAME if i + 1 < len(merged) else 1e9
            j = i - 1 if gap_prev <= gap_next else i + 1
            lo, hi = min(merged[j][0], a), max(merged[j][1], b)
            if min(gap_prev, gap_next) < 0.8 and (hi - lo) * FRAME <= MAX_LINE:
                merged[min(i, j)] = [lo, hi]
                del merged[max(i, j)]
                i = max(0, min(i, j))
                continue
        i += 1
    return [(round(a * FRAME, 2), round(b * FRAME, 2)) for a, b in merged if (b - a) * FRAME >= 0.35]


class StreamSegmenter:
    """segment() for audio that is still arriving. Samples are appended to one growing buffer;
    every ~60 s of new audio, the not-yet-cut part is segmented and the pieces that end well before
    the current end (so a pause really follows them) are released. The rest waits for more audio."""

    FIRST = 25.0      # first cut as soon as this much audio is in (first subtitles early)
    EVERY = 60.0      # then every this many seconds of new audio
    TAIL = 6.0        # pieces must end this long before the end of the audio so far
    MAX_WINDOW = 240.0

    def __init__(self, duration: float = 0.0):
        try:
            duration = float(duration)
            if not math.isfinite(duration) or not 0 <= duration <= MAX_HOURS * 3600:
                raise ValueError
        except (ValueError, TypeError, OverflowError):
            raise AudioError(f"音频时长无效或超过 {MAX_HOURS} 小时") from None
        self.max_samples = int(MAX_HOURS * 3600 * SR)
        self.pcm = np.zeros(min(self.max_samples, int((duration + 10) * SR) if duration else SR * 600), dtype=np.int16)
        self.n = 0                  # samples filled
        self.commit = 0.0           # seconds: everything before this is cut into released pieces
        self.next_cut = self.FIRST
        self.lock = threading.Lock()

    @property
    def seconds(self) -> float:
        return self.n / SR

    def add(self, block: np.ndarray) -> list[tuple[float, float]]:
        with self.lock:
            if self.n + len(block) > self.max_samples:
                raise AudioError(f"实际音频超过 {MAX_HOURS} 小时，已停止识别")
            if self.n + len(block) > len(self.pcm):          # longer than expected: grow
                bigger = np.zeros(min(self.max_samples, max(len(self.pcm) * 2, self.n + len(block))), dtype=np.int16)
                bigger[:self.n] = self.pcm[:self.n]
                self.pcm = bigger
            self.pcm[self.n:self.n + len(block)] = block
            self.n += len(block)
        if self.seconds < self.next_cut:
            return []
        self.next_cut = self.seconds + self.EVERY
        return self._cut(final=False)

    def finish(self) -> list[tuple[float, float]]:
        return self._cut(final=True)

    def _cut(self, final: bool) -> list[tuple[float, float]]:
        start, end = self.commit, self.seconds
        if end - start < 1.0:
            return []
        a = int(start * SR)
        pieces = [(round(s + start, 2), round(e + start, 2)) for s, e in segment(self.pcm[a:self.n])]
        if not final:
            pieces = [p for p in pieces if p[1] <= end - self.TAIL]
            if pieces:
                self.commit = pieces[-1][1]
            elif end - start > self.MAX_WINDOW:            # long music / silence: move on
                self.commit = end - self.TAIL
        else:
            self.commit = end
        return pieces

    def view(self) -> np.ndarray:
        """The samples so far (shares memory with the buffer)."""
        return self.pcm[:self.n]


# ------------------------------------------------------- recognition ---

class RateLimiter:
    """Spaces requests evenly to stay under the API's requests-per-minute limit (shared by all jobs)."""

    def __init__(self, per_minute: float):
        self.interval = 60.0 / per_minute
        self.next = 0.0
        self.lock = threading.Lock()

    def wait(self, cancelled=None):
        with self.lock:
            now = time.monotonic()
            t = max(now, self.next)
            self.next = t + self.interval
        while t > time.monotonic():
            if cancelled and cancelled():
                raise A.GenerationCancelled("任务已取消")
            time.sleep(min(0.05, max(0, t - time.monotonic())))


_client = None
_client_config = None
_client_lock = threading.Lock()
_usage_lock = threading.Lock()
_cache_locks = [threading.Lock() for _ in range(64)]
ASR_SLOTS = threading.BoundedSemaphore(4)
LIMIT = RateLimiter(float(os.environ.get("ASR_RPM", 90)))   # MiMo: 100 RPM per account


def reset_client():
    global _client, _client_config
    with _client_lock:
        _client = None
        _client_config = None


def usage_snapshot(usage: dict) -> dict:
    with _usage_lock:
        return audio_totals(usage) if usage else {}


def configured() -> bool:
    return bool(os.environ.get("MIMO_API_KEY"))


def client():
    global _client, _client_config
    with _client_lock:
        config = (os.environ["MIMO_API_KEY"], os.environ.get("MIMO_BASE_URL") or "https://api.xiaomimimo.com/v1")
        if _client is None or config != _client_config:
            from openai import OpenAI
            _client = OpenAI(api_key=config[0], base_url=config[1], max_retries=0, timeout=40)
            _client_config = config
        return _client


def wav_bytes(samples: np.ndarray) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(samples.tobytes())
    return buf.getvalue()


MAX_TRANSCRIPT_CHARS = 20000
MAX_TRANSCRIPT_CACHE_BYTES = 128 * 1024
TRANSCRIPT_CACHE_VERSION = 2  # Only confirmed complete, UTF-8 encodable responses.


def valid_transcript(text):
    if not isinstance(text, str) or len(text) > MAX_TRANSCRIPT_CHARS or (text and not text.strip()):
        return False
    try:
        text.encode("utf-8")
    except UnicodeError:
        return False
    return True


def cached_transcript(path):
    """None means unusable cache; an empty string is a valid saved silence result."""
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_TRANSCRIPT_CACHE_BYTES + 1)
        if len(raw) > MAX_TRANSCRIPT_CACHE_BYTES:
            return None
        value = json.loads(raw.decode("utf-8"))
        if (isinstance(value, dict) and set(value) == {"version", "text"}
                and type(value["version"]) is int and value["version"] == TRANSCRIPT_CACHE_VERSION and valid_transcript(value["text"])):
            return value["text"]
    except (OSError, ValueError, RecursionError):
        pass
    return None


def transcribe(pcm: np.ndarray, s: float, e: float, usage: dict | None = None,
               pad_before: float = 0.15, pad_after: float = 0.15, *, client_instance=None,
               model=None, language=None, cancelled=None) -> str:
    """Recognise one piece. A little padding keeps edge words from being clipped, but only into a
    real pause: where a long line was cut mid-speech, padding would make both halves hear the word."""
    a, b = max(0, int((s - pad_before) * SR)), min(len(pcm), int((e + pad_after) * SR))
    if b <= a:
        raise AudioError("音频片段为空")
    model = model or os.environ.get("ASR_MODEL", "mimo-v2.5-asr")
    language = language or os.environ.get("ASR_LANGUAGE", "auto")
    service = client_instance or client()
    raw = wav_bytes(pcm[a:b])
    digest = hashlib.sha256(json.dumps([TRANSCRIPT_CACHE_VERSION, model, language, str(getattr(service, "base_url", ""))]).encode() + raw).hexdigest()
    cache_file = A.CACHE_DIR / "asr" / f"{digest}.json"
    with A.request_slot(_cache_locks[int(digest[:2], 16) % len(_cache_locks)], cancelled):
        cached = cached_transcript(cache_file)
        if cached is not None:
            return cached
        with A.request_slot(ASR_SLOTS, cancelled):
            LIMIT.wait(cancelled)
            with A.request_slot(A.MODEL_SLOTS, cancelled):
                data = base64.b64encode(raw).decode("ascii")
                r = service.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": [{"type": "input_audio", "input_audio": {"data": f"data:audio/wav;base64,{data}"}}]}],
                    extra_body={"asr_options": {"language": language}},
                )
        if usage is not None:
            seconds, estimated = audio_seconds(r, (b - a) / SR)
            with _usage_lock:
                previous = audio_totals(usage)
                usage.update(asr_calls=previous["asr_calls"] + 1,
                             asr_seconds=previous["asr_seconds"] + seconds,
                             asr_estimated_calls=previous["asr_estimated_calls"] + int(estimated))
        choices = getattr(r, "choices", None)
        if not isinstance(choices, list) or len(choices) != 1:
            raise AudioError("语音识别返回的结果格式不正确，请重试")
        choice = choices[0]
        text = getattr(getattr(choice, "message", None), "content", None)
        reason = getattr(choice, "finish_reason", None)
        if reason == "content_filter" or is_refusal(text):
            raise ContentRefused(text)
        if reason != "stop":
            raise AudioError("语音识别结果未确认完整结束，请重试")
        if not valid_transcript(text) or not text:
            raise AudioError("语音识别没有返回有效文本，请重试")
        text = fix_i(strip_tags(re.sub(r"\s+", " ", text).strip()))
        try:
            write_json(cache_file, {"version": TRANSCRIPT_CACHE_VERSION, "text": text})
        except OSError:
            print("语音识别缓存保存失败，本次结果仍可使用")
        return text


# Labels the recognisers put out instead of words when there is no speech: MiMo writes its language tag
# ("<chinese>", sometimes "(chinese)") for cheering, music and noise; Whisper writes "[Music]", "(applause)".
TAG_RE = re.compile(r"<\s*/?\s*[A-Za-z_|]{1,24}\s*>"
                    r"|[(\[]\s*(?:chinese|english|music|applause|laughter|laughs|laughing|cheering|cheers|noise|"
                    r"silence|inaudible|blank_audio|no speech|crowd|singing|sighs|background noise)\s*[)\]]"
                    r"|\[\s*[^\[\]]{1,30}\s*\]|[♪♫]+", re.I)


def strip_tags(text: str) -> str:
    """Remove non-speech labels; "" if nothing that looks like words is left."""
    t = re.sub(r"\s+", " ", TAG_RE.sub(" ", text or "")).strip(" -–")
    return t if re.search(r"[A-Za-z一-鿿]", t) else ""


class ContentRefused(Exception):
    """The ASR service's content filter refused this piece (it answers with a refusal sentence, not words)."""


# MiMo's refusal sentence, and close variants (a backstop in case finish_reason is not set)
REFUSAL_RE = re.compile(r"\s*(?:the )?request (?:was|has been) (?:rejected|refused|blocked)"
                        r" because it was considered (?:as )?high[- ]risk[.!]?\s*", re.I)


def is_refusal(text: str) -> bool:
    return isinstance(text, str) and bool(REFUSAL_RE.fullmatch(text))


def fix_i(text: str) -> str:
    """The model sometimes writes "i" for "I" ("So, i decided", "i'm")."""
    return re.sub(r"\bi\b(?![.]e[.])", "I", text)


def pads(pieces: list[tuple[float, float]], i: int) -> tuple[float, float]:
    """Padding for piece i: up to 0.15 s, never more than half the pause to its neighbours."""
    s, e = pieces[i]
    before = min(0.15, (s - pieces[i - 1][1]) / 2) if i > 0 else 0.15
    after = min(0.15, (pieces[i + 1][0] - e) / 2) if i + 1 < len(pieces) else 0.15
    return max(0.0, before), max(0.0, after)


def mostly_chinese(text: str) -> bool:
    cjk = len(re.findall(r"[一-鿿]", text))
    return cjk >= 2 and cjk > 0.5 * len(re.findall(r"[A-Za-z]", text))


def cost_cny(seconds: float) -> float:
    return seconds / 3600 * PRICE_PER_HOUR_CNY
