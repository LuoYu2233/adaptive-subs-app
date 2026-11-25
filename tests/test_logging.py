import io
import threading

import applog
import server


def test_split_secrets_are_masked_before_console_file_and_memory(monkeypatch):
    monkeypatch.setenv("MIMO_API_KEY", "private-test-key-abcdef")
    console, file = io.StringIO(), io.StringIO()
    stream = applog._Tee(console, file)
    stream.write("failure private-test-")
    assert console.getvalue() == file.getvalue() == ""
    stream.write("key-abcdef; Authorization: Bearer sk-example-secret-123\n")
    for output in (console.getvalue(), file.getvalue(), str(applog.tail(1))):
        assert "private-test" not in output and "sk-example" not in output
        assert "[REDACTED]" in output


def test_api_errors_are_redacted_before_shortening(monkeypatch):
    secret = "k" * 200
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    safe = server.short_err(ValueError("Incorrect API key: " + secret))
    assert "kkkk" not in safe and "[REDACTED]" in safe
    assert "secret" not in applog.redact("Cookie: session=secret; other=value")
    assert "secret" not in applog.redact("https://user:secret@example.invalid/?access_token=private")


def test_running_logs_rotate_and_keep_only_one_previous_file(tmp_path):
    path = tmp_path / "adaptive-subs.log"
    sink = applog._RotatingFile(path, max_bytes=100)
    for _ in range(20):
        sink.write("line " + "x" * 30 + "\n")
    sink.close()
    assert path.stat().st_size <= 100
    assert (tmp_path / "adaptive-subs.old.log").stat().st_size <= 100
    assert len(list(tmp_path.iterdir())) == 2


def test_long_lines_and_file_failures_do_not_break_worker_logging():
    class Broken:
        def write(self, _):
            raise OSError("disk full")
    console = io.StringIO()
    stream = applog._Tee(console, Broken())
    stream.write("x" * 70000)
    stream.write("unfinished\nnext normal line\n")
    assert "已省略" in console.getvalue() and "next normal line" in console.getvalue()
    assert "unfinished" not in console.getvalue() and len(stream.buf) < 65536
    threads = [threading.Thread(target=stream.write, args=(f"worker-{i}\n",)) for i in range(20)]
    for t in threads: t.start()
    for t in threads: t.join(timeout=1)
    assert all(f"worker-{i}\n" in console.getvalue() for i in range(20))
