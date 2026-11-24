"""Installed extension check against synthetic YouTube/Bilibili pages and the real local server.

Called by browser_check.py. External platform requests are fulfilled locally or blocked.
This covers extension wiring and interactions, not compatibility with the live websites.
"""
import json
import base64
import io
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check_extension(playwright, base, home, output):
    from playwright.sync_api import expect

    extension = ROOT / "extension"
    context = playwright.chromium.launch_persistent_context(str(home / "chrome-extension-test"),
        channel="chromium", headless=True, viewport={"width": 1100, "height": 800},
        args=[f"--disable-extensions-except={extension}", f"--load-extension={extension}"])
    try:
        worker = context.service_workers[0] if context.service_workers else context.wait_for_event("serviceworker")
        worker.evaluate("base => chrome.storage.session.set({base})", base)
        worker.evaluate("() => chrome.storage.local.set({asubsKeysHint: 1})")
        data = json.loads((home / "data/zzzzzzzzzzz.json").read_text())
        bili = home / "data/extension/bili_BV1234567890_123_en.json"
        bili.parent.mkdir(exist_ok=True)
        bili.write_text(json.dumps(data | {"video": data["video"] | {"id": bili.stem}}))
        external = []
        fail_mark = False
        fail_profile = False
        delay_lookup = False
        pending_lookup = []
        # A real media timeline crosses the page/content-script isolated-world boundary.
        sound = io.BytesIO()
        with wave.open(sound, "wb") as wav:
            wav.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
            wav.writeframes(b"\0\0" * 80000)
        audio_url = "data:audio/wav;base64," + base64.b64encode(sound.getvalue()).decode()

        def route_request(route):
            url = route.request.url
            if url.startswith(base + "/"):
                if delay_lookup and "/api/define?w=" in url:
                    if url.endswith("w=approved"):
                        return route.fulfill(json={"word": "approved", "zh": ""})
                    pending_lookup.append(route)
                    return
                if delay_lookup and url.endswith("/api/explain"):
                    pending_lookup.append(route)
                    return
                if (fail_mark and url.endswith("/api/mark")) or (fail_profile and url.endswith("/api/profile") and route.request.method == "POST"):
                    return route.fulfill(status=507, content_type="application/json", body=json.dumps({"error": "测试磁盘不可写"}))
                return route.continue_()
            if url.startswith("chrome-extension://"):
                return route.continue_()
            external.append(url)
            if url.startswith("https://www.youtube.com/watch") or url.startswith("https://www.bilibili.com/video/"):
                youtube = "youtube" in url
                host = 'id="movie_player"' if youtube else 'id="bilibili-player" class="bpx-player-container"'
                html = f'''<!doctype html><html><head><meta charset="utf-8"><title>原创插件测试</title>
                  <style>body{{margin:30px;background:#202733;color:white;font:18px system-ui}} .fixture{{position:relative;width:960px;height:540px;background:#121820}} video{{width:960px;height:540px}}</style>
                  </head><body><p>Adaptive Subs 插件回归 · 原创句子 / 模拟页面</p>
                  <div {host}><div class="fixture"><video class="html5-main-video" src="{audio_url}" preload="auto"></video></div></div>
                  </body></html>'''
                return route.fulfill(content_type="text/html", body=html,
                    headers={"Content-Security-Policy": "require-trusted-types-for 'script'"})
            result = None
            if "/x/web-interface/view?" in url:
                result = {"title": "原创词义测试", "owner": {"mid": 1, "name": "测试"}, "pages": [{"page": 1, "cid": 123, "duration": 10}]}
            elif "/x/web-interface/nav" in url:
                result = {"isLogin": True, "wbi_img": {"img_url": "https://example.com/" + "a" * 32 + ".png", "sub_url": "https://example.com/" + "b" * 32 + ".png"}}
            elif "/x/player/" in url:
                result = {"subtitle": {"subtitles": [{"lan": "en", "lan_doc": "英语", "subtitle_url": "https://test.hdslb.com/sub.json"}]}}
            elif url == "https://test.hdslb.com/sub.json":
                return route.fulfill(content_type="application/json", body=json.dumps({"body": [
                    {"from": c["s"], "to": c["e"], "content": c["text"]} for c in data["cues"]]}))
            if result is not None:
                return route.fulfill(content_type="application/json", body=json.dumps({"code": 0, "data": result}),
                    headers={"Access-Control-Allow-Origin": "https://www.bilibili.com", "Access-Control-Allow-Credentials": "true"})
            return route.abort()

        context.route("**/*", route_request)
        for site, url in (("youtube", "https://www.youtube.com/watch?v=zzzzzzzzzzz"),
                          ("bilibili", "https://www.bilibili.com/video/BV1234567890")):
            result = worker.evaluate("() => handle({type:'setprofile',payload:{reset_marks:true}})")
            assert not result.get("error"), result
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(url)
            page.wait_for_function("document.querySelector('video').readyState >= 1")
            gloss = page.locator(".asubs-gz")
            expect(gloss).to_have_text("银行", timeout=20000)
            expect(page.locator(".asubs-w")).to_have_text("bank")
            # Delayed lookups for an earlier word must never alter a later card's mark key.
            delay_lookup = True
            page.locator('.asubs-t[data-w="approved"]').click()
            for _ in range(200):
                if any("/api/define?" in route.request.url for route in pending_lookup):
                    break
                page.wait_for_timeout(10)
            else:
                raise AssertionError("The lemma lookup was not observed")
            page.locator(".asubs-t").filter(has_text="bank").click()
            expect(page.locator(".asubs-card-word")).to_have_text("bank")
            for route in pending_lookup:
                route.fulfill(json={"word": "approve", "zh": "批准", "short": "批准", "base": "approve", "pos": "v.", "note": ""})
            pending_lookup.clear()
            delay_lookup = False
            page.wait_for_timeout(150)
            expect(page.locator(".asubs-card-zh")).to_contain_text("银行")
            page.locator('.asubs-card [data-act="known"]').click()
            expect(page.locator(".asubs-pill")).to_contain_text("已加入熟词库")
            marked = json.loads((home / "profile.json").read_text())
            assert len(marked["known"]) == 1 and marked["known"][0].startswith("ctx:bank:"), marked["known"]
            assert next(iter(marked["mark_details"].values()))["zh"] == "银行"
            worker.evaluate("() => handle({type:'setprofile',payload:{reset_marks:true}})")
            page.reload()
            expect(gloss).to_have_text("银行", timeout=20000)
            page.locator(".asubs-t").filter(has_text="bank").click()
            expect(page.locator(".asubs-card-zh")).to_contain_text("银行")
            fail_mark = True
            page.locator('.asubs-card [data-act="known"]').click()
            expect(page.locator(".asubs-pill")).to_contain_text("标记保存未确认")
            expect(gloss).to_have_text("银行")
            fail_mark = False
            page.locator(".asubs-t").filter(has_text="bank").click()
            page.locator('.asubs-card [data-act="known"]').click()
            expect(page.locator(".asubs-pill")).to_contain_text("已加入熟词库")
            expect(gloss).to_have_count(0)
            page.locator("video").evaluate("v => { v.currentTime = 6; }")
            expect(gloss).to_have_text("河岸")
            page.locator(".asubs-t").filter(has_text="bank").click()
            expect(page.locator(".asubs-card-zh")).to_contain_text("河岸")
            page.locator('.asubs-card [data-act="unknown"]').click()
            expect(page.locator(".asubs-pill")).to_contain_text("已标为生词")
            saved = json.loads((home / "profile.json").read_text())
            assert len(saved["known"]) == len(saved["unknown"]) == 1
            assert {x["zh"] for x in saved["mark_details"].values()} == {"银行", "河岸"}
            page.screenshot(path=str(output / f"extension-{site}.png"), full_page=True)
            assert not errors, errors
            page.close()
        popup = context.new_page()
        popup.goto(worker.url.rsplit("/", 1)[0] + "/popup.html")
        expect(popup.locator("#levelValue")).to_contain_text("4,500")
        fail_profile = True
        popup.locator("#level").focus()
        popup.locator("#level").press("End")
        expect(popup.locator("#saveStatus")).to_contain_text("画像未保存")
        expect(popup.locator("#levelValue")).to_contain_text("4,500")
        fail_profile = False
        popup.locator("#level").press("Home")
        expect(popup.locator("#saveStatus")).to_contain_text("已保存到本机画像")
        assert json.loads((home / "profile.json").read_text())["level"] == 1500
        # Requests are queued in the service worker, including server discovery.
        result = worker.evaluate("() => Promise.all([2000, 6000, 5000].map(level => handle({type:'setprofile',payload:{level}})))")
        assert [r["level"] for r in result] == [2000, 6000, 5000]
        assert result[0]["rev"] < result[1]["rev"] < result[2]["rev"]
        assert json.loads((home / "profile.json").read_text())["level"] == 5000
        popup.reload()
        expect(popup.locator("#levelValue")).to_contain_text("5,000")
        popup.screenshot(path=str(output / "extension-popup.png"), full_page=True)
        (output / "extension-requests.json").write_text(json.dumps(external, ensure_ascii=False, indent=2))
        print("Installed extension checks passed on synthetic YouTube/Bilibili pages: overlay, meaning cards, failed-save rollback and separate meaning feedback.")
    except Exception:
        for i, page in enumerate(context.pages):
            page.screenshot(path=str(output / f"extension-failure-{i}.png"), full_page=True)
        raise
    finally:
        context.close()
