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
import io
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import wave
from pathlib import Path

import numpy as np

SR = 16000
FRAME = 0.03                 # analysis frame, seconds
MAX_LINE = 7.0               # longest piece (one subtitle line)
MIN_PAUSE = 0.3              # a pause at least this long ends a line
MAX_HOURS = 3                # refuse longer videos (memory: ~115 MB of samples per hour)
PRICE_PER_HOUR_CNY = 0.5     # MiMo V2.5 ASR list price


class AudioError(Exception):
    pass


# ------------------------------------------------------------- audio ---

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
        if (b - a) * FRAME <= MAX_LINE:
            pieces.append((a, b))
            return
        lo, hi = a + int(1.5 / FRAME), b - int(1.5 / FRAME)
        k = lo + int(np.argmin(smooth[lo:hi]))
        split(a, k)
        split(k, b)

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


# ------------------------------------------------------- recognition ---

class RateLimiter:
    """Spaces requests evenly to stay under the API's requests-per-minute limit (shared by all jobs)."""

    def __init__(self, per_minute: float):
        self.interval = 60.0 / per_minute
        self.next = 0.0
        self.lock = threading.Lock()

    def wait(self):
        with self.lock:
            now = time.monotonic()
            t = max(now, self.next)
            self.next = t + self.interval
        if t > now:
            time.sleep(t - now)


_client = None
_client_lock = threading.Lock()
LIMIT = RateLimiter(float(os.environ.get("ASR_RPM", 90)))   # MiMo: 100 RPM per account


def reset_client():
    global _client
    with _client_lock:
        _client = None


def configured() -> bool:
    return bool(os.environ.get("MIMO_API_KEY"))


def client():
    global _client
    with _client_lock:
        if _client is None:
            from openai import OpenAI
            _client = OpenAI(api_key=os.environ["MIMO_API_KEY"],
                             base_url=os.environ.get("MIMO_BASE_URL", "https://api.xiaomimimo.com/v1"),
                             max_retries=3, timeout=40)
        return _client


def wav_bytes(samples: np.ndarray) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(samples.tobytes())
    return buf.getvalue()


def transcribe(pcm: np.ndarray, s: float, e: float, usage: dict | None = None,
               pad_before: float = 0.15, pad_after: float = 0.15) -> str:
    """Recognise one piece. A little padding keeps edge words from being clipped, but only into a
    real pause: where a long line was cut mid-speech, padding would make both halves hear the word."""
    a, b = max(0, int((s - pad_before) * SR)), min(len(pcm), int((e + pad_after) * SR))
    data = base64.b64encode(wav_bytes(pcm[a:b])).decode("ascii")
    LIMIT.wait()
    r = client().chat.completions.create(
        model=os.environ.get("ASR_MODEL", "mimo-v2.5-asr"),
        messages=[{"role": "user", "content": [{"type": "input_audio", "input_audio": {"data": f"data:audio/wav;base64,{data}"}}]}],
        extra_body={"asr_options": {"language": os.environ.get("ASR_LANGUAGE", "auto")}},
    )
    if usage is not None and r.usage is not None:
        u = r.usage.model_dump()
        usage["asr_calls"] = usage.get("asr_calls", 0) + 1
        usage["asr_seconds"] = usage.get("asr_seconds", 0) + (u.get("seconds") or round(b / SR - a / SR))
    text = re.sub(r"\s+", " ", r.choices[0].message.content or "").strip()
    return fix_i(text)


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
