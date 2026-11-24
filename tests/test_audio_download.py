import io
import threading
import urllib.request
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import numpy as np
import pytest

import asr


class Response(io.BytesIO):
    def __init__(self, body=b"", status=200, **headers):
        super().__init__(body)
        self.status, self.headers = status, headers


def read_all(download):
    output = b""
    while len(output) < download.size:
        output += download.read_at(len(output), download.size - len(output))
    return output


def finish_read(download):
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(read_all, download)
        try:
            return future.result(timeout=2)
        finally:
            download.cancel()  # A regression must fail instead of hanging the suite.
            for thread in download.threads:
                thread.join(timeout=2)


def test_truncated_whole_download_wakes_reader_with_error(monkeypatch):
    monkeypatch.setattr(asr.ChunkedDownload, "_open", lambda *a, **k: Response(b"1234", **{"Content-Length": "10"}))
    download = asr.ChunkedDownload("https://example.invalid/audio", {})
    with pytest.raises(asr.AudioError, match="提前结束"):
        finish_read(download)


def test_oversized_response_cannot_expand_reserved_buffer(monkeypatch):
    monkeypatch.setattr(asr.ChunkedDownload, "_open", lambda *a, **k: Response(b"x" * 50, **{"Content-Length": "10"}))
    download = asr.ChunkedDownload("https://example.invalid/audio", {})
    with pytest.raises(asr.AudioError, match="大小.*变化"):
        finish_read(download)
    assert len(download.buf) == 10


@pytest.mark.parametrize("size", ["0", "-3", "unknown", str(384 * 1024 * 1024 + 1), "9" * 500])
def test_bad_size_fails_before_buffer_allocation(monkeypatch, size):
    monkeypatch.setattr(asr.ChunkedDownload, "_open", lambda *a, **k: Response(**{"Content-Length": size}))
    with pytest.raises(asr.AudioError):
        asr.ChunkedDownload("https://example.invalid/audio", {})


def test_valid_whole_stream_finishes(monkeypatch):
    monkeypatch.setattr(asr.ChunkedDownload, "_open", lambda *a, **k: Response(b"1234567890", **{"Content-Length": "10"}))
    assert finish_read(asr.ChunkedDownload("https://example.invalid/audio", {})) == b"1234567890"


def test_parallel_ranges_are_checked_and_reads_are_bounded(monkeypatch):
    audio, sizes = b"original audio fixture", []
    class Tracked(Response):
        def read(self, n=-1):
            assert n > 0
            sizes.append(n)
            return super().read(n)
    def open_range(self, start=None, end=None, **kwargs):
        return Tracked(audio[start:end + 1], status=206, **{"Content-Range": f"bytes {start}-{end}/{len(audio)}"})
    monkeypatch.setattr(asr.ChunkedDownload, "_open", open_range)
    download = asr.ChunkedDownload("https://example.invalid/audio", {}, workers=2, chunk=8)
    assert finish_read(download) == audio
    assert sizes and max(sizes) <= 9


def test_wrong_range_is_rejected_without_reading_body(monkeypatch):
    monkeypatch.setattr(asr, "CHUNK_TRIES", 1)
    class WrongRange(Response):
        def read(self, n=-1):
            raise AssertionError("Wrong ranges must not be read")
    def open_range(self, start=None, end=None, **kwargs):
        return WrongRange(status=206, **{"Content-Range": "bytes 0-0/10"})
    monkeypatch.setattr(asr.ChunkedDownload, "_open", open_range)
    download = asr.ChunkedDownload("https://example.invalid/audio", {}, workers=1, chunk=8)
    with pytest.raises(asr.AudioError, match="分段范围"):
        finish_read(download)


def test_invalid_reader_seek_does_not_change_position():
    reader = asr._Reader(SimpleNamespace(size=10))
    assert reader.seek(4) == 4
    with pytest.raises(ValueError):
        reader.seek(-20, io.SEEK_CUR)
    assert reader.tell() == 4
    with pytest.raises(ValueError):
        reader.seek(0, 999)
    reader.close()


@pytest.mark.parametrize("duration", [-1, float("nan"), float("inf"), asr.MAX_HOURS * 3600 + 1])
def test_invalid_duration_cannot_allocate_pcm(duration):
    with pytest.raises(asr.AudioError):
        asr.StreamSegmenter(duration)


def test_actual_audio_duration_and_growth_are_bounded(monkeypatch):
    monkeypatch.setattr(asr, "SR", 10)
    monkeypatch.setattr(asr, "MAX_HOURS", 0.5)
    segmenter = asr.StreamSegmenter()
    monkeypatch.setattr(segmenter, "_cut", lambda final: [])
    segmenter.add(np.ones(6500, dtype=np.int16))
    segmenter.add(np.ones(6000, dtype=np.int16))
    assert segmenter.n == 12500 and len(segmenter.pcm) == 18000
    with pytest.raises(asr.AudioError, match="实际音频"):
        segmenter.add(np.ones(6000, dtype=np.int16))
    assert segmenter.n == 12500 and len(segmenter.pcm) == 18000


def test_long_unbroken_speech_does_not_overflow_python_recursion(monkeypatch):
    monkeypatch.setattr(asr, "SR", 10)
    monkeypatch.setattr(asr, "FRAME", 1.0)
    pcm = np.zeros(100000, dtype=np.int16)
    pcm[20000:] = 1000
    pieces = asr.segment(pcm)
    assert len(pieces) > 1000
    assert all(0 < end - start <= asr.MAX_LINE for start, end in pieces)
    assert all(pieces[i][1] <= pieces[i + 1][0] for i in range(len(pieces) - 1))


def test_real_http_ranges_decode_original_wav_with_pyav(monkeypatch):
    av = pytest.importorskip("av", reason="PyAV integration needs full application dependencies")
    samples = (np.sin(np.arange(1600) * 2 * np.pi * 440 / asr.SR) * 10000).astype(np.int16)
    stream = io.BytesIO()
    with wave.open(stream, "wb") as wav:
        wav.setparams((1, 2, asr.SR, 0, "NONE", "not compressed"))
        wav.writeframes(samples.tobytes())
    audio = stream.getvalue()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            start, end = map(int, self.headers["Range"].removeprefix("bytes=").split("-"))
            body = audio[start:end + 1]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(audio)}")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    monkeypatch.setattr(urllib.request, "urlopen", opener.open)
    download = None
    try:
        download = asr.ChunkedDownload(f"http://127.0.0.1:{httpd.server_port}/audio.wav", {}, chunk=512)
        with asr._Reader(download) as reader, av.open(reader, mode="r") as container:
            decoded = np.concatenate([frame.to_ndarray().reshape(-1) for frame in container.decode(audio=0)])
        np.testing.assert_array_equal(decoded, samples)
    finally:
        if download:
            download.cancel()
            for worker in download.threads:
                worker.join(timeout=2)
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)
