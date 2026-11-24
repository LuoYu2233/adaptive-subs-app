"""Exercise missing local components, protected generation and repair in a real browser."""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = """
import os,sys
from pathlib import Path
sys.path.insert(0,'app')
import server as S
home=Path(os.environ['ASUBS_HOME'])
original_nlp=S.A.nlp
original_ranks=S.A.load_ranks
def nlp():
    if not (home/'repaired').exists():
        raise ImportError('Synthetic missing English model')
    return original_nlp()
def ranks(*args,**kwargs):
    if not (home/'ranks-repaired').exists():
        raise ImportError('Synthetic missing word frequency component')
    return original_ranks(*args,**kwargs)
ranks.cache_clear=original_ranks.cache_clear
def forbidden(*args,**kwargs):
    (home/'unexpected-request').write_text('A blocked generation reached a provider or platform function')
    raise AssertionError('No provider or platform requests are permitted in this check')
S.A.nlp=nlp
S.A.load_ranks=ranks
S.P.client=S.ASR.client=S.fetch_subtitles=forbidden
sys.argv=['server.py','--port',sys.argv[1]]
S.main()
"""


def main():
    from playwright.sync_api import sync_playwright, expect
    output = ROOT / "test-results/runtime"
    output.mkdir(parents=True, exist_ok=True)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with tempfile.TemporaryDirectory(prefix="asubs-runtime-") as temp:
        home = Path(temp)
        original_profile = b'{"rev":2,"known":["bank"],"identity":"undergrad"}'
        (home / "profile.json").write_bytes(original_profile)
        (home / "data").mkdir()
        saved = {"video": {"id": "zzzzzzzzzzz", "title": "Original saved sample", "duration": 1},
                 "cues": [{"s": 0, "e": 1, "text": "Original sample.", "notes": []}]}
        (home / "data/zzzzzzzzzzz.json").write_text(json.dumps(saved))
        env = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY")}
        env.update(ASUBS_HOME=temp, ASUBS_NO_BROWSER="1", ASUBS_HEADLESS="1", ANNOTATION_PROVIDER="deepseek",
                   DEEPSEEK_API_KEY="synthetic-test-key-never-used", MIMO_API_KEY="synthetic-asr-key-never-used")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        base = f"http://127.0.0.1:{port}"

        def request(path, body=None, protected=True):
            headers = {"X-Adaptive-Subs": "1", "Content-Type": "application/json"} if protected else {"Content-Type": "application/json"}
            req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                headers=headers, method="POST" if body is not None else "GET")
            try:
                response = opener.open(req, timeout=5)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                return response.status, json.load(response)

        with (output / "server.log").open("w", encoding="utf-8") as log:
            child = subprocess.Popen([sys.executable, "-u", "-c", SCRIPT, str(port)], cwd=ROOT, env=env,
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
            try:
                for _ in range(100):
                    if child.poll() is not None: raise RuntimeError("Runtime test server exited")
                    try:
                        status, health = request("/api/health")
                        if health.get("runtime", {}).get("state") == "error": break
                    except OSError: pass
                    time.sleep(.1)
                else: raise RuntimeError("Missing component was not reported")
                assert health["runtime"]["component"] == "英语语言模型"
                assert request("/api/runtime/check", {}, protected=False)[0] == 403
                for path, body in (("/api/open", {"url": "abcdefghijk"}),
                    ("/api/ext/open", {"id": "yt_abcdefghijk_asr", "source": "asr", "yt": "abcdefghijk"})):
                    status, result = request(path, body)
                    assert status == 409 and "本地英语处理组件尚未就绪" in result["error"], (status, result)
                status, cached = request("/api/open", {"url": "zzzzzzzzzzz"})
                assert status == 200 and cached["cached"] is True
                assert request("/api/profile")[0] == 503
                assert request("/api/profile/export")[1]["profile"]["known"] == ["bank"]
                assert request("/api/status")[1]["jobs"] == []
                assert not (home / "unexpected-request").exists()
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(headless=True)
                    page = browser.new_page(viewport={"width": 1280, "height": 900}, reduced_motion="reduce")
                    page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(base + "/") else route.abort())
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(base + "/app")
                    expect(page.locator("#runtimeHelp")).to_be_visible()
                    expect(page.locator("#stageBody")).to_contain_text("本地组件需要修复")
                    expect(page.locator("#d5")).to_contain_text("英语语言模型")
                    expect(page.locator("#num")).to_have_text("暂不可用")
                    expect(page.locator("#b3")).not_to_have_text("恢复画像")
                    page.screenshot(path=str(output / "missing-component.png"), full_page=True, animations="disabled")
                    page.set_viewport_size({"width": 390, "height": 844})
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                    assert page.locator("#runtimeRetry").evaluate("button => button.getBoundingClientRect().right <= innerWidth")
                    page.screenshot(path=str(output / "missing-component-mobile.png"), full_page=True, animations="disabled")
                    page.set_viewport_size({"width": 1280, "height": 900})
                    page.locator("#runtimeRetry").click()
                    expect(page.locator("#runtimeRetry")).to_be_enabled()
                    assert request("/api/health")[1]["runtime"]["state"] == "error"
                    (home / "repaired").touch()
                    page.locator("#runtimeRetry").click()
                    expect(page.locator("#d5")).to_contain_text("英语词频表", timeout=30000)
                    (home / "ranks-repaired").touch()
                    page.locator("#runtimeRetry").click()
                    expect(page.locator("#d5")).to_contain_text("已就绪", timeout=30000)
                    expect(page.locator("#runtimeHelp")).to_be_hidden()
                    expect(page.locator("#stageBody")).to_contain_text("一切就绪")
                    assert request("/api/health")[1]["runtime"]["state"] == "ready"
                    assert not (home / "unexpected-request").exists()
                    assert (home / "profile.json").read_bytes() == original_profile
                    assert not errors, errors
                    page.screenshot(path=str(output / "repaired-component.png"), full_page=True, animations="disabled")
                    browser.close()
                report = {"passed": True, "missing_component_visible": True,
                    "new_annotation_and_cloud_asr_blocked": True, "saved_subtitles_and_profile_backup_available": True,
                    "missing_word_library_keeps_control_panel_available": True,
                    "repair_loads_actual_local_english_model": True, "provider_or_platform_calls": 0}
                (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
                print(json.dumps(report, indent=2))
            finally:
                child.terminate()
                try: child.wait(timeout=5)
                except subprocess.TimeoutExpired: child.kill(); child.wait(timeout=5)


if __name__ == "__main__":
    main()
