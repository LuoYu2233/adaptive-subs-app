"""Exercise real CAT exports, packaged extension, localhost backend, feedback fit and blind scoring together."""
import argparse
import base64
import io
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import Request, urlopen
import wave
from zipfile import ZipFile

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from check_cat_page import check as measured_trial


def fixture(ident, text):
    notes = []
    for word in ("orbit", "plume", "bridge", "drift"):
        pos = text.index(word)
        notes.append({"s": pos, "e": pos + len(word), "kind": "word", "key": word, "lv": 9000 - len(notes),
                      "zh": {"orbit": "轨道", "plume": "羽毛", "bridge": "桥", "drift": "漂移"}[word]})
    return {"video": {"id": ident, "title": "联合验收", "duration": 5}, "cues": [{"s": 0, "e": 5, "text": text, "zh": "", "notes": notes}]}


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def measure_from_archive(archive, table):
    with tempfile.TemporaryDirectory() as temp, sync_playwright() as pw:
        folder = Path(temp) / "试用 资料"
        with ZipFile(archive) as z:
            assert z.testzip() is None
            z.extractall(folder)
        digest = sha256((folder / "word-difficulty-v1.json").read_bytes()).hexdigest()
        assert digest == sha256(table.read_bytes()).hexdigest()
        guide = (folder / "先打开这里.html").as_uri()
        browser = pw.chromium.launch()
        try:
            context = browser.new_context(offline=True, accept_downloads=True)
            page = context.new_page()
            unexpected = []
            page.on("pageerror", lambda err: unexpected.append(str(err)))
            page.on("request", lambda req: unexpected.append(req.url) if req.url.startswith(("http:", "https:")) else None)
            page.goto(guide)
            page.get_by_role("link", name="开始单词试测").click()
            page.locator("#prior").fill("5000")
            page.locator("#prior").press("Enter")
            for i in range(40):
                if not page.locator("#quiz").is_visible(): break
                page.keyboard.press("0" if i % 3 == 0 else "1")
            expect(page.locator("#done")).to_be_visible()
            measured = {}
            for item in ("parameters", "record"):
                with page.expect_download() as export:
                    page.locator("#" + item).click()
                measured[item] = json.loads(Path(export.value.path()).read_bytes())
            assert measured["record"]["itemBank"]["count"] == 800
            assert measured["record"]["parameters"] == measured["parameters"]
            assert measured["parameters"]["tableSha256"] == digest

            page.goto(guide)
            page.get_by_role("link", name="开始短语试测").click()
            assert len(json.loads(page.locator("#data").text_content())["items"]) == 100
            page.locator("#begin").click()
            for i in range(8):
                if not page.locator("#quiz").is_visible(): break
                page.keyboard.press("0" if i % 2 else "3")
            expect(page.locator("#complete")).to_be_focused()
            with page.expect_download() as export:
                page.locator("#record").click()
            phrase = json.loads(Path(export.value.path()).read_bytes())
            assert 6 <= len(phrase["responses"]) <= 8 and phrase["model"] == "3PL phrase CAT"
            assert not unexpected, unexpected
            return measured
        finally:
            browser.close()


def trial(table, archive, analysis_python, materials=None):
    measured = measure_from_archive(materials, table) if materials else measured_trial(table, expanded=True)
    with tempfile.TemporaryDirectory() as temp:
        folder = Path(temp)
        home = folder / "独立用户"
        (home / "data").mkdir(parents=True)
        training, blind = "studyset001", "blindset002"
        save(home / "data" / f"{training}.json", fixture(training, "orbit plume bridge drift"))
        save(home / "data" / f"{blind}.json", fixture(blind, "😀 orbit plume bridge drift today"))
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        if archive:
            with ZipFile(archive) as z: z.extractall(folder / "分发包")
            program = folder / "分发包" / "AdaptiveSubs-Windows"
            command = [str(program / "程序" / "AdaptiveSubs.exe")]
            ext = program / "浏览器插件"
        else:
            command = [sys.executable, str(ROOT / "app/server.py")]
            ext = ROOT / "extension"
        env = dict(os.environ, ASUBS_HOME=str(home), ASUBS_HEADLESS="1", ASUBS_NO_BROWSER="1", PYTHONIOENCODING="utf-8")
        for key in ("OPENAI_API_KEY","DEEPSEEK_API_KEY","MIMO_API_KEY"): env.pop(key, None)
        with (folder / "server.log").open("wb") as log:
            server = subprocess.Popen(command + ["--port", str(port)], env=env, stdout=log, stderr=subprocess.STDOUT)
            try:
                for _ in range(200):
                    if server.poll() is not None: raise RuntimeError("Desktop program exited during startup")
                    try:
                        with urlopen(base + "/api/health", timeout=2) as r:
                            if json.load(r)["ok"]: break
                    except OSError: pass
                    time.sleep(.25)
                else: raise RuntimeError("Local server did not start")
                try:
                    fit = capture(folder, ext, base, table, measured, training, blind, analysis_python)
                except Exception:
                    print((folder / "server.log").read_text(encoding="utf-8", errors="replace")[-4000:])
                    raise
                save(folder / "clips.json", {"version": 1, "clips": [{"clip": "holdout", "domain": "科技", "packet": "blind.json"}]})
                result = json.loads(subprocess.check_output(["node", str(ROOT / "tools/score_blind_feedback.cjs"),
                    "fit.json", str(table), "clips.json", "normal"], cwd=folder))
                assert result["updated"]["probability"]["observations"] == 3
                assert result["updated"]["display"]["hits"] + result["updated"]["display"]["missed"] == 1
                assert result["tableSha256"] == measured["record"]["parameters"]["tableSha256"]
                save(folder / "cohort.json", {"version": 1, "sessions": [{"learner": "P01", "domain": "科技",
                    "packet": "blind.json", "parameters": "parameters.json"}]})
                cohort = json.loads(subprocess.check_output(["node", str(ROOT / "tools/compare_word_cohort.cjs"),
                    "cohort.json", str(table), "normal"], cwd=folder))
                assert cohort["participants"] == 1 and cohort["videos"] == 1
                assert cohort["overall"]["models"][fit["field"]]["probability"] == result["anchor"]["probability"]
                reimport(folder, ext, base, table, blind, fit)
                print("Passed real 800-item CAT, 30000-word import, locally persisted correction, sealed feedback fit, later blind scoring and in-player calibrated probability.")
            finally:
                if server.poll() is None:
                    server.terminate()
                    try: server.wait(timeout=12)
                    except subprocess.TimeoutExpired: server.kill(); server.wait(timeout=10)


def reimport(folder, ext, base, table, blind, fit):
    estimate = fit["estimate"]
    theta = estimate["theta"] - estimate["viewingDiscount"] + estimate["domainBias"]["科技"]
    difficulty = json.loads(table.read_bytes())["words"]["plume"][fit["field"]]
    expected = 1 / (1 + math.exp(-fit["anchor"]["discrimination"] * (difficulty - theta)))
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(str(folder / "浏览器"), channel="chromium", headless=True,
            args=[f"--disable-extensions-except={ext}", f"--load-extension={ext}"])
        try:
            def route(r):
                if r.request.url.startswith("https://www.youtube.com/watch?"):
                    return r.fulfill(content_type="text/html", body='<div id="movie_player" style="height:400px"><video muted></video></div>')
                if r.request.url.startswith(("chrome-extension://", base + "/")): return r.continue_()
                return r.abort()
            ctx.route("**/*", route)
            worker = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker")
            worker.evaluate("b => chrome.storage.session.set({base:b})", base)
            page = ctx.new_page()
            page.goto(worker.url.rsplit("/", 1)[0] + "/irt.html")
            page.locator("#table").set_input_files(table)
            page.locator("#parameters").set_input_files(folder / "parameters.json")
            page.locator("details summary").click()
            page.locator("#feedback").set_input_files(folder / "fit.json")
            page.locator("#domain").select_option("科技")
            page.locator("#apply").click()
            expect(page.locator("#status")).to_contain_text("观看校准（科技，1 段）")
            state = worker.evaluate("""async () => {
                const {irtPreview: m} = await chrome.storage.local.get("irtPreview");
                return {p: m.probabilities.plume, policy: m.policy};
            }""")
            assert math.isclose(state["p"], expected, abs_tol=1e-12)
            assert state["policy"]["trainedThrough"] == fit["trainingCutoff"]
            video = ctx.new_page()
            video.goto("https://www.youtube.com/watch?v=" + blind)
            video.locator(".asubs-t[data-n='1']").click()
            expect(video.locator(".asubs-card-why")).to_contain_text("观看校准 · 科技")
            expect(video.locator(".asubs-card-why")).to_contain_text(f"{expected * 100:.1f}%")
        finally:
            ctx.close()


def capture(folder, ext, base, table, measured, training, blind, analysis_python):
    save(folder / "cat.json", measured["record"])
    save(folder / "parameters.json", measured["parameters"])
    audio = io.BytesIO()
    with wave.open(audio, "wb") as wav:
        wav.setparams((1, 2, 8000, 0, "NONE", "")); wav.writeframes(bytes(8000 * 2 * 6))
    src = "data:audio/wav;base64," + base64.b64encode(audio.getvalue()).decode()
    html = f'<div id="movie_player" style="width:800px;height:400px"><video muted style="width:100%;height:100%" src="{src}"></video></div>'
    with sync_playwright() as pw:
        args = [f"--disable-extensions-except={ext}", f"--load-extension={ext}", "--autoplay-policy=no-user-gesture-required"]
        ctx = pw.chromium.launch_persistent_context(str(folder / "浏览器"), channel="chromium", headless=True, args=args)
        try:
            unexpected = []
            def route(r):
                u = r.request.url
                if u.startswith("https://www.youtube.com/watch?"): return r.fulfill(content_type="text/html", body=html)
                if u.startswith(base + "/") or u.startswith("chrome-extension://"): return r.continue_()
                unexpected.append(u); return r.abort()
            ctx.route("**/*", route)
            worker = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker")
            origin = worker.url.rsplit("/", 1)[0]
            worker.evaluate("b => chrome.storage.session.set({base:b})",base)
            worker.evaluate("() => chrome.storage.local.set({settings:{pref:'normal'},asubsKeysHint:1})")
            settings = ctx.new_page(); settings.goto(origin + "/irt.html")
            settings.locator("#table").set_input_files(table)
            settings.locator("#parameters").set_input_files(folder / "parameters.json")
            settings.locator("#apply").click()
            expect(settings.locator("#status")).to_contain_text("30,000")
            video = ctx.new_page(); video.goto("https://www.youtube.com/watch?v=" + training)
            expect(video.locator(".asubs-t[data-n]")).to_have_count(4)
            video.bring_to_front()
            video.locator(".asubs-t[data-n='0']").click()
            expect(video.locator(".asubs-card-why")).to_contain_text("IRT 试用", timeout=20000)
            video.locator(".asubs-card [data-act=close]").click()
            tab = worker.evaluate("async () => (await chrome.tabs.query({active:true,currentWindow:true}))[0].id")
            study = ctx.new_page(); study.goto(origin + "/study.html?tab=" + str(tab))
            study.locator("#clip").fill("train01"); study.locator("#domain").fill("科技"); study.locator("#begin").click()
            expect(study.locator("#status")).to_contain_text("正在记录")
            video.bring_to_front(); video.evaluate("document.querySelector('video').play()")
            expect(study.locator("#status")).to_contain_text("4 次")
            video.locator(".asubs-t[data-n='3']").click()
            video.locator(".asubs-card [data-act=unknown]").click()
            expect(video.locator(".asubs-pill")).to_contain_text("已标为生词")
            with urlopen(Request(base + "/api/profile", headers={"X-Adaptive-Subs":"1"})) as r:
                assert "drift" in json.load(r)["marks"]["unknown"]
            study.locator("#stop").click()
            with study.expect_download() as f: study.locator("#export").click()
            f.value.save_as(folder / "study.json")
            assert len(json.loads((folder / "study.json").read_bytes())["exposures"]) == 4
            subprocess.run([analysis_python, str(ROOT / "tools/fit_viewing_feedback.py"), str(folder / "cat.json"),
                str(table), "--train", str(folder / "study.json"), "--output", str(folder / "fit.json")], check=True, cwd=folder)
            fit = json.loads((folder / "fit.json").read_bytes())
            assert fit["training"]["included"] == 4 and fit["trainingSubtitleHashes"] and fit["trainingMediaHashes"]
            video.goto("https://www.youtube.com/watch?v=" + blind)
            expect(video.locator(".asubs-t[data-n]")).to_have_count(4)
            label = ctx.new_page(); label.goto(origin + "/label.html?tab=" + str(tab))
            label.locator("#new").click()
            label.get_by_role("button", name="plume", exact=True).click()
            label.locator("#confirm").click()
            expect(label.locator("#bundle")).to_be_enabled()
            with label.expect_download() as f: label.locator("#bundle").click()
            f.value.save_as(folder / "blind.json")
            packet = json.loads((folder / "blind.json").read_bytes())
            assert packet["profile"]["marks"]["unknown"] == ["drift"]
            assert packet["labels"]["cues"][0]["unknown"] == [{"s":8,"text":"plume"}]
            assert not unexpected, unexpected
            assert fit["trainingCutoff"] <= fit["frozenAt"] <= packet["labels"]["collection"]["startedAt"]
            return fit
        finally:
            ctx.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("table", type=Path)
    p.add_argument("--archive", type=Path)
    p.add_argument("--materials", type=Path)
    p.add_argument("--analysis-python", default=sys.executable)
    args = p.parse_args()
    trial(args.table.resolve(), args.archive, args.analysis_python, args.materials)
