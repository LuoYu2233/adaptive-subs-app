"""Optional local English ASR fallback: CPU int8, no model downloads during recognition.

The model loads on first use and is unloaded after 5 idle minutes. Accuracy and speed
depend on the audio and computer; synthetic smoke results are not a quality benchmark.
"""

from __future__ import annotations

import os
import math
import sys
import threading
import time
from pathlib import Path

import numpy as np

SR = 16000
MODEL_NAME = "faster-whisper-base.en"
REQUIRED_FILES = ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt")
IDLE_UNLOAD = 300            # seconds without use before the model is freed

_lock = threading.Lock()       # one recognition at a time (the model uses several threads itself)
_model = None
_model_path = None
_last_used = 0.0
_timer: threading.Timer | None = None


def model_dir() -> Path:
    if os.environ.get("LOCAL_ASR_MODEL_DIR"):
        return Path(os.environ["LOCAL_ASR_MODEL_DIR"]).expanduser().resolve()
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))      # packaged program: inside the bundle
    return base / "models" / MODEL_NAME


def available() -> bool:
    if missing_files():
        return False
    try:
        import faster_whisper  # noqa: F401
        return True
    except Exception:
        return False


def missing_files() -> list[str]:
    path = model_dir()
    try:
        return [name for name in REQUIRED_FILES if not (path / name).is_file() or (path / name).stat().st_size == 0]
    except OSError:
        return list(REQUIRED_FILES)


def _threads() -> int:
    return max(1, min(4, (os.cpu_count() or 2) // 2))       # leave the other half for the video and browser


def _get_model():
    global _model, _model_path
    path = model_dir()
    missing = missing_files()
    if missing:
        raise RuntimeError("本地语音模型文件不完整：" + ", ".join(missing))
    if _model is None or _model_path != path:
        from faster_whisper import WhisperModel
        t = time.time()
        # Missing tokenizer.json otherwise causes faster-whisper to fetch a tokenizer online,
        # even when its local_files_only flag is set. Check all files before constructing it.
        model = WhisperModel(str(path), device="cpu", compute_type="int8", cpu_threads=_threads(),
                             local_files_only=True)
        _model, _model_path = model, path
        print(f"  local speech model loaded ({MODEL_NAME}, {_threads()} threads) in {time.time() - t:.1f}s")
    return _model


def _schedule_unload():
    global _timer
    if _timer:
        _timer.cancel()
    _timer = threading.Timer(IDLE_UNLOAD + 1, _unload_if_idle)
    _timer.daemon = True
    _timer.start()


def _unload_if_idle():
    global _model
    with _lock:
        if _model is not None and time.time() - _last_used >= IDLE_UNLOAD:
            _model = None
            print("  local speech model unloaded (idle)")


def transcribe(pcm: np.ndarray, s: float, e: float, pad_before: float = 0.15, pad_after: float = 0.15) -> str:
    """Recognise pcm[s-pad, e+pad] (16 kHz mono int16) -> English text ("" for no speech)."""
    global _last_used
    if not isinstance(pcm, np.ndarray) or pcm.ndim != 1 or pcm.dtype != np.int16:
        raise ValueError("本地语音识别需要 16 kHz 单声道 int16 音频")
    if not all(math.isfinite(x) and x >= 0 for x in (s, e, pad_before, pad_after)) or e < s:
        raise ValueError("本地语音识别时间范围无效")
    a, b = max(0, int((s - pad_before) * SR)), min(len(pcm), int((e + pad_after) * SR))
    audio = pcm[a:b].astype(np.float32) / 32768.0
    if len(audio) < SR * 0.2 or not np.any(audio):
        return ""
    with _lock:
        model = _get_model()
        try:
            parts, _ = model.transcribe(audio, language="en", beam_size=1, vad_filter=False,
                                        condition_on_previous_text=False, without_timestamps=True)
            text = " ".join(p.text.strip() for p in parts).strip()
        finally:
            _last_used = time.time()
            _schedule_unload()
    return text
