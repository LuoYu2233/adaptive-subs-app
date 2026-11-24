import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import sys
import threading
from unittest.mock import Mock

import pytest

import server as S


def health(**changes):
    return {"ok": True, "app": "adaptive-subs", "version": S.VERSION, "ext": 2,
            "setup": "/setup", "profile": "/profile", **changes}


@pytest.mark.parametrize("change", [{"ok": "yes"}, {"app": "other-app"}, {"version": "bad version"},
    {"ext": True}, {"ext": 0}, {"setup": "https://outside.invalid"}, {"profile": "//outside.invalid"}])
def test_product_discovery_rejects_unrelated_or_malformed_health(change):
    assert S.valid_health(health())
    assert not S.valid_health(health(**change))
    assert not S.valid_health({"ok": True})
    legacy = health()
    del legacy["app"]
    assert S.valid_health(legacy)


@pytest.mark.parametrize("kind", ["current", "other", "http_error", "too_large", "redirect"])
def test_real_loopback_probe_checks_identity_status_size_and_never_follows_redirects(kind):
    paths = []
    class Service(BaseHTTPRequestHandler):
        def do_GET(self):
            paths.append(self.path)
            if kind == "redirect" and self.path != "/redirected":
                self.send_response(302)
                self.send_header("Location", "/redirected")
                self.end_headers()
                return
            value = {"ok": True} if kind == "other" else health()
            if kind == "too_large": value["padding"] = "x" * 16384
            raw = json.dumps(value).encode()
            self.send_response(503 if kind == "http_error" else 200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        def log_message(self, *args): pass
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Service)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        result = S.probe_server(*httpd.server_address)
        assert bool(result) is (kind == "current")
        assert paths == ["/api/health"]
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("value", ["0", "-1", "65536", "port", "1.5"])
def test_invalid_port_rejected_by_argument_parser(value):
    with pytest.raises(argparse.ArgumentTypeError): S.port_number(value)
    assert S.port_number("65535") == 65535


@pytest.mark.parametrize("version", [S.VERSION, "0.3.0"])
def test_startup_cannot_silently_reuse_an_unprotected_old_process(tmp_path, monkeypatch, version):
    monkeypatch.setattr(S, "HOME", tmp_path)
    monkeypatch.setattr(S.A, "load_dotenv", lambda *args: None)
    monkeypatch.setattr(S.applog, "install", lambda *args: None)
    bind = Mock(side_effect=OSError("occupied"))
    window = Mock()
    monkeypatch.setattr(S, "Server", bind)
    monkeypatch.setattr(S, "probe_server", lambda *args: health(version=version))
    monkeypatch.setattr(S, "run_window", window)
    monkeypatch.setattr(sys, "argv", ["server.py", "--gui"])
    with pytest.raises(RuntimeError, match="请先退出"): S.main()
    window.assert_not_called()
    assert bind.call_count == 1


def test_last_valid_port_does_not_scan_outside_tcp_port_range(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "HOME", tmp_path)
    monkeypatch.setattr(S.A, "load_dotenv", lambda *args: None)
    monkeypatch.setattr(S.applog, "install", lambda *args: None)
    bind = Mock(side_effect=OSError("occupied"))
    monkeypatch.setattr(S, "Server", bind)
    monkeypatch.setattr(S, "probe_server", lambda *args: None)
    monkeypatch.setattr(sys, "argv", ["server.py", "--port", "65535"])
    with pytest.raises(SystemExit, match="65535 and 65535"): S.main()
    bind.assert_called_once_with(("127.0.0.1", 65535), S.Handler)
