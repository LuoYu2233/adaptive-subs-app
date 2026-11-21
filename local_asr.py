"""Local speech recognition for the lines the cloud recogniser refuses.

MiMo's ASR runs every input through a content filter and refuses whole lines over a single word
(swearing, "kill", "naked" ... normal in films and talks): it then answers with finish_reason
"content_filter" and a refusal sentence instead of the words. Those lines are recognised here, on
this computer, with Whisper base.en (faster-whisper, int8 on the CPU — no GPU needed).

Measured on the refused lines of three videos (2 CPU threads): ~1 s per line, +~120 MB memory while
loaded, nearly word-perfect. The model loads on first use and is unloaded after 5 idle minutes.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import numpy as np

SR = 16000
MODEL_NAME = "faster-whisper-base.en"
IDLE_UNLOAD = 300            # seconds without use before the model is freed

_lock = threading.Lock()       # one recognition at a time (the model uses several threads itself)
_model = None
_last_used = 0.0
_timer: threading.Timer | None = None


def model_dir() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))      # packaged program: inside the bundle
    return base / "models" / MODEL_NAME


def available() -> bool:
    if not (model_dir() / "model.bin").exists():
        return False
    try:
        import faster_whisper  # noqa: F401
        return True
    except Exception:
        return False


def _threads() -> int:
    return max(1, min(4, (os.cpu_count() or 2) // 2))       # leave the other half for the video and browser


def _get_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        t = time.time()
        _model = WhisperModel(str(model_dir()), device="cpu", compute_type="int8", cpu_threads=_threads())
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
    a, b = max(0, int((s - pad_before) * SR)), min(len(pcm), int((e + pad_after) * SR))
    audio = pcm[a:b].astype(np.float32) / 32768.0
    if len(audio) < SR * 0.2:
        return ""
    with _lock:
        model = _get_model()
        parts, _ = model.transcribe(audio, language="en", beam_size=1, vad_filter=False,
                                    condition_on_previous_text=False, without_timestamps=True)
        text = " ".join(p.text.strip() for p in parts).strip()
        _last_used = time.time()
    _schedule_unload()
    return text
