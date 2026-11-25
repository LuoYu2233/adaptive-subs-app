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
        data = json.loads((home / "data/zzzzzzzzzzz.json").read_text(encoding="utf-8"))
        bili = home / "data/extension/bili_BV1234567890_123_en.json"
        bili.parent.mkdir(exist_ok=True)
        bili.write_text(json.dumps(data | {"video": data["video"] | {"id": bili.stem}}), encoding="utf-8")
        external = []
        fail_mark = False
        fail_profile = False
        delay_lookup = False
        mismatched_version = False
        missing_runtime = False
        offline = False
        profile_read_mode = None
        pending_profile_reads = []
        profile_posts = []
        profile_write_mode = None
        pending_profile_writes = []
        pending_lookup = []
        # A real media timeline crosses the page/content-script isolated-world boundary.
        sound = io.BytesIO()
        with wave.open(sound, "wb") as wav:
            wav.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
            wav.writeframes(b"\0\0" * 80000)
        audio_url = "data:audio/wav;base64," + base64.b64encode(sound.getvalue()).decode()

        def route_request(route):
            url = route.request.url
            if url == "http://127.0.0.1:1/api/health":
                return route.fulfill(json={"ok": True})
            if url.startswith(base + "/"):
                if url.endswith("/api/profile") and route.request.method == "POST":
                    profile_posts.append(route.request.post_data_json)
                    if profile_write_mode == "lost":
                        response = route.fetch()
                        assert response.status == 200
                        assert json.loads((home / "profile.json").read_text(encoding="utf-8"))["level"] == route.request.post_data_json["level"]
                        return route.abort("failed")
                    if profile_write_mode == "malformed":
                        return route.fulfill(json={"level": route.request.post_data_json["level"]})
                    if profile_write_mode == "pending":
                        pending_profile_writes.append(route)
                        return
                if url.endswith("/api/profile?summary=1") and profile_read_mode:
                    if profile_read_mode == "component":
                        return route.fulfill(status=503, json={"error": "测试英语词汇组件不可用", "component": True})
                    if profile_read_mode == "http": return route.fulfill(status=500, json={})
                    if profile_read_mode == "malformed": return route.fulfill(json={"level": 5000})
                    if profile_read_mode == "null": return route.fulfill(json=None, body="null", content_type="application/json")
                    pending_profile_reads.append(route)
                    return
                if offline and url.endswith("/api/health"):
                    return route.fulfill(status=503, json={})
                if (mismatched_version or missing_runtime) and url.endswith("/api/health"):
                    version = "0.3.0" if mismatched_version else json.loads((extension / "manifest.json").read_text(encoding="utf-8"))["version_name"]
                    return route.fulfill(json={"ok": True, "app": "adaptive-subs", "version": version, "ext": 2,
                        "setup": "/setup", "profile": "/profile", "key": True, "provider_name": "模拟服务商",
                        "runtime": {"state": "error", "message": "英语语言模型无法正常加载"} if missing_runtime else {"state": "ready"}})
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
        assert worker.evaluate("() => health('http://127.0.0.1:1')") is None
        for site, url in (("youtube", "https://www.youtube.com/watch?v=zzzzzzzzzzz"),
                          ("bilibili", "https://www.bilibili.com/video/BV1234567890")):
            result = worker.evaluate("async () => { const p = await handle({type:'profile'}); return handle({type:'setprofile',payload:{reset_marks:true,expected_rev:p.rev}}); }")
            assert not result.get("error"), result
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(url)
            page.wait_for_function("document.querySelector('video').readyState >= 1")
            gloss = page.locator(".asubs-gz")
            expect(gloss).to_have_text("银行", timeout=20000)
            expect(page.locator(".asubs-w")).to_have_text("bank")
            # Storage changes after the content script loaded use the same validation.
            worker.evaluate("() => chrome.storage.local.set({settings: {enabled: 'false', level: null, mode: 'unknown', pref: 'more'}})")
            expect(gloss).to_have_text("银行")
            assert worker.evaluate("() => chrome.storage.local.get('settings')")["settings"]["level"] is None
            worker.evaluate("() => chrome.storage.local.set({settings: {enabled: true, mode: 'plain'}, profileRev: Date.now()})")
            expect(gloss).to_have_count(0)
            worker.evaluate("() => chrome.storage.local.set({settings: {enabled: true, mode: 'adaptive'}})")
            expect(gloss).to_have_text("银行")
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
            marked = json.loads((home / "profile.json").read_text(encoding="utf-8"))
            assert len(marked["known"]) == 1 and marked["known"][0].startswith("ctx:bank:"), marked["known"]
            assert next(iter(marked["mark_details"].values()))["zh"] == "银行"
            worker.evaluate("async () => { const p = await handle({type:'profile'}); return handle({type:'setprofile',payload:{reset_marks:true,expected_rev:p.rev}}); }")
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
            saved = json.loads((home / "profile.json").read_text(encoding="utf-8"))
            assert len(saved["known"]) == len(saved["unknown"]) == 1
            assert {x["zh"] for x in saved["mark_details"].values()} == {"银行", "河岸"}
            page.screenshot(path=str(output / f"extension-{site}.png"), full_page=True)
            assert not errors, errors
            page.close()
        popup = context.new_page()
        popup_errors = []
        popup.on("pageerror", lambda error: popup_errors.append(str(error)))
        popup.on("console", lambda message: popup_errors.append(message.text)
                 if message.type == "error" and "Error handling response" in message.text else None)
        popup.goto(worker.url.rsplit("/", 1)[0] + "/popup.html")
        expect(popup.locator("#levelValue")).to_contain_text("4,500")
        fail_profile = True
        popup.locator("#level").focus()
        popup.locator("#level").press("End")
        expect(popup.locator("#saveStatus")).to_contain_text("保存结果未确认")
        expect(popup.locator("#levelValue")).to_contain_text("4,500")
        fail_profile = False
        popup.locator("#level").press("Home")
        expect(popup.locator("#saveStatus")).to_contain_text("已保存到本机画像")
        assert json.loads((home / "profile.json").read_text(encoding="utf-8"))["level"] == 1500
        saved_preferences = worker.evaluate("() => chrome.storage.local.get('settings')")
        profile_write_mode = "malformed"
        before = len(profile_posts)
        popup.locator("#level").press("End")
        expect(popup.locator("#saveStatus")).to_contain_text("保存结果未确认")
        expect(popup.locator("#levelValue")).to_contain_text("1,500")
        assert len(profile_posts) == before + 1
        assert worker.evaluate("() => chrome.storage.local.get('settings')") == saved_preferences
        profile_write_mode = "lost"
        before = len(profile_posts)
        popup.locator("#level").press("End")
        expect(popup.locator("#saveStatus")).to_contain_text("当前词汇量与本次设置一致")
        expect(popup.locator("#levelValue")).to_contain_text("15,000")
        assert len(profile_posts) == before + 1  # The real saved write was not replayed.
        assert json.loads((home / "profile.json").read_text(encoding="utf-8"))["level"] == 15000
        # A lost write response followed by a failed read is uncertain, never a saved/unsaved claim.
        profile_read_mode = "component"
        before = len(profile_posts)
        popup.locator("#level").press("Home")
        expect(popup.locator("#saveStatus")).to_contain_text("保存结果尚未确认")
        expect(popup.locator("#level")).to_be_disabled()
        expect(popup.locator("#profileText")).to_contain_text("不会自动重发")
        assert json.loads((home / "profile.json").read_text(encoding="utf-8"))["level"] == 1500
        assert len(profile_posts) == before + 1
        profile_read_mode = None
        popup.locator("#retryProfile").click()
        expect(popup.locator("#levelValue")).to_contain_text("1,500")
        expect(popup.locator("#level")).to_be_enabled()
        profile_write_mode = "pending"
        worker.evaluate("""() => {
            globalThis.originalWriteTimeout = AbortSignal.timeout;
            AbortSignal.timeout = ms => originalWriteTimeout(ms === 10000 ? 500 : ms);
        }""")
        before = len(profile_posts)
        popup.locator("#level").press("End")
        expect(popup.locator("#level")).to_be_disabled()
        popup.locator("#level").evaluate("e => { e.value = 0; e.dispatchEvent(new Event('input')); e.dispatchEvent(new Event('change')); }")
        expect(popup.locator("#saveStatus")).to_contain_text("保存结果未确认")
        expect(popup.locator("#level")).to_be_enabled()
        expect(popup.locator("#levelValue")).to_contain_text("1,500")
        assert len(profile_posts) == before + 1
        worker.evaluate("AbortSignal.timeout = globalThis.originalWriteTimeout; delete globalThis.originalWriteTimeout")
        for route in pending_profile_writes: route.abort("failed")
        pending_profile_writes.clear()
        profile_write_mode = None
        popup.locator("#level").press("End")
        expect(popup.locator("#saveStatus")).to_contain_text("已保存到本机画像")
        assert json.loads((home / "profile.json").read_text(encoding="utf-8"))["level"] == 15000
        # Requests are queued in the service worker, including server discovery.
        result = worker.evaluate("() => Promise.all([2000, 6000, 5000].map(level => handle({type:'setprofile',payload:{level}})))")
        assert [r["level"] for r in result] == [2000, 6000, 5000]
        assert result[0]["rev"] < result[1]["rev"] < result[2]["rev"]
        assert json.loads((home / "profile.json").read_text(encoding="utf-8"))["level"] == 5000
        popup.reload()
        expect(popup.locator("#levelValue")).to_contain_text("5,000")
        # Connected service with an unavailable profile must not become an offline write.
        before_read_failure = (home / "profile.json").read_bytes()
        before_browser_settings = worker.evaluate("() => chrome.storage.local.get('settings')")
        before_profile_posts = len(profile_posts)
        for profile_read_mode in ("component", "http", "malformed", "null"):
            popup.reload()
            expect(popup.locator("#profileText")).to_contain_text("画像暂时无法读取")
            expect(popup.locator("#levelValue")).to_have_text("暂不可用")
            expect(popup.locator("#levelNote")).to_contain_text("已连接程序")
            expect(popup.locator("#level")).to_be_disabled()
            expect(popup.locator("#retryProfile")).to_be_visible()
            popup.locator("#level").evaluate("e => { e.value = 11; e.dispatchEvent(new Event('input')); e.dispatchEvent(new Event('change')); }")
            assert worker.evaluate("() => chrome.storage.local.get('settings')") == before_browser_settings
            assert (home / "profile.json").read_bytes() == before_read_failure
            assert len(profile_posts) == before_profile_posts
        with context.expect_page() as opened:
            popup.locator("#openProfile").click()
        profile_page = opened.value
        profile_page.wait_for_url(base + "/profile")
        profile_page.close()
        popup.screenshot(path=str(output / "extension-profile-read-error.png"), full_page=True)
        profile_read_mode = "pending"
        popup.locator("#retryProfile").click()
        expect(popup.locator("#levelValue")).to_have_text("读取中…")
        expect(popup.locator("#level")).to_be_disabled()
        expect(popup.locator("#profileBox")).to_be_hidden()
        for _ in range(100):
            if pending_profile_reads: break
            popup.wait_for_timeout(10)
        assert len(pending_profile_reads) == 1
        good_profile = worker.evaluate("() => handle({type:'profile'})")
        pending_profile_reads.pop().fulfill(json=good_profile)
        expect(popup.locator("#levelValue")).to_contain_text("5,000")
        expect(popup.locator("#level")).to_be_enabled()
        expect(popup.locator("#retryProfile")).to_be_hidden()
        # Exercise a real AbortSignal in the worker, compressing only this read deadline.
        worker.evaluate("""() => {
            globalThis.originalProfileTimeout = AbortSignal.timeout;
            AbortSignal.timeout = ms => originalProfileTimeout(ms === 10000 ? 250 : ms);
        }""")
        popup.reload()
        expect(popup.locator("#profileText")).to_contain_text("画像暂时无法读取")
        expect(popup.locator("#level")).to_be_disabled()
        worker.evaluate("AbortSignal.timeout = globalThis.originalProfileTimeout; delete globalThis.originalProfileTimeout")
        for route in pending_profile_reads: route.abort("failed")
        pending_profile_reads.clear()
        profile_read_mode = None
        popup.locator("#retryProfile").click()
        expect(popup.locator("#levelValue")).to_contain_text("5,000")
        assert len(profile_posts) == before_profile_posts
        assert (home / "profile.json").read_bytes() == before_read_failure
        mismatched_version = True
        popup.reload()
        expect(popup.locator("#status")).to_contain_text("与插件版本不同")
        expect(popup.locator("#status")).to_contain_text("0.3.0")
        mismatched_version = False
        missing_runtime = True
        popup.reload()
        expect(popup.locator("#status")).to_contain_text("英语语言模型无法正常加载")
        expect(popup.locator("#openSetup")).to_have_text("打开控制台")
        with context.expect_page() as opened:
            popup.locator("#openSetup").click()
        control = opened.value
        control.wait_for_url(base + "/app")
        control.close()
        missing_runtime = False
        popup.reload()
        expect(popup.locator("#status")).not_to_contain_text("与插件版本不同")
        before_profile = (home / "profile.json").read_bytes()
        broken_settings = {"level": None, "enabled": "false", "mode": "unknown", "pref": "less"}
        worker.evaluate("settings => chrome.storage.local.set({settings})", broken_settings)
        offline = True
        popup.reload()
        expect(popup.locator("#status")).to_contain_text("程序没开", timeout=20000)
        expect(popup.locator("#levelValue")).to_contain_text("3,000")
        expect(popup.locator("#level")).to_be_enabled()
        expect(popup.locator("#profileBox")).to_be_hidden()
        expect(popup.locator("#coverHere")).to_be_hidden()
        expect(popup.locator("#settingsIssue")).to_contain_text("原始存储尚未修改")
        expect(popup.locator('[data-key="pref"] [data-v="less"]')).to_have_attribute("aria-checked", "true")
        expect(popup.locator('[data-key="mode"] [data-v="adaptive"]')).to_have_attribute("aria-checked", "true")
        assert worker.evaluate("() => chrome.storage.local.get('settings')")["settings"] == broken_settings
        assert (home / "profile.json").read_bytes() == before_profile
        popup.screenshot(path=str(output / "extension-settings-recovery.png"), full_page=True)
        # Use an actual Chrome quota rejection in this isolated browser profile.
        popup.evaluate("""() => {
            const original = chrome.storage.local.set;
            const quota = chrome.storage.local.QUOTA_BYTES;
            if (!Number.isInteger(quota) || quota <= 0 || quota > 20 * 1024 * 1024) throw new Error('Unexpected test storage quota');
            window.restorePreferenceWrites = () => { chrome.storage.local.set = original; };
            chrome.storage.local.set = (_, callback) => original.call(chrome.storage.local,
                {asubsQuotaFailureFixture: 'x'.repeat(quota + 1)}, callback);
        }""")
        popup.locator('[data-key="mode"] [data-v="bilingual"]').click()
        expect(popup.locator("#settingsIssue")).to_contain_text("未能保存")
        assert worker.evaluate("() => chrome.storage.local.get('settings')")["settings"] == broken_settings
        assert worker.evaluate("() => chrome.storage.local.get('asubsQuotaFailureFixture')") == {}
        popup.evaluate("restorePreferenceWrites(); delete window.restorePreferenceWrites")
        popup.locator('[data-key="mode"] [data-v="plain"]').click()
        expect(popup.locator("#settingsIssue")).to_be_hidden()
        stored = worker.evaluate("() => chrome.storage.local.get('settings')")["settings"]
        assert stored["mode"] == "plain" and stored["pref"] == "less" and stored["level"] == 3000
        popup.locator("#level").focus()
        popup.locator("#level").press("End")
        expect(popup.locator("#saveStatus")).to_contain_text("已保存在此浏览器")
        assert worker.evaluate("() => chrome.storage.local.get('settings')")["settings"]["level"] == 15000
        assert (home / "profile.json").read_bytes() == before_profile
        assert not popup_errors, popup_errors
        offline = False
        worker.evaluate("() => chrome.storage.local.set({settings: {}})")
        popup.reload()
        expect(popup.locator("#levelValue")).to_contain_text("5,000")
        popup.screenshot(path=str(output / "extension-popup.png"), full_page=True)
        (output / "extension-requests.json").write_text(json.dumps(external, ensure_ascii=False, indent=2), encoding="utf-8")
        print("Installed extension checks passed on synthetic YouTube/Bilibili pages: overlay, meaning cards, failed-save rollback and separate meaning feedback.")
    except Exception:
        for i, page in enumerate(context.pages):
            page.screenshot(path=str(output / f"extension-failure-{i}.png"), full_page=True)
        raise
    finally:
        context.close()
