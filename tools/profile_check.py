"""Exercise real vocabulary-test completion and uncertain save recovery without model calls."""
import json
from pathlib import Path


def check_profile(context, base, home, output):
    from playwright.sync_api import expect

    page = context.new_page()
    errors, posts, pending = [], [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    headers = {"X-Adaptive-Subs": "1"}
    original = page.request.get(base + "/api/profile/export", headers=headers).json()
    mode = "reject"
    fail_read = False
    fail_read_after_write = False
    export_override = None
    read_mode = None
    pending_reads, all_profile_posts = [], []

    def save_response(route):
        nonlocal fail_read
        if route.request.method != "POST":
            if read_mode == "component": return route.fulfill(status=503, json={"error": "测试英语词库暂不可用", "component": True})
            if read_mode == "malformed": return route.fulfill(json={})
            if read_mode == "null": return route.fulfill(body="null", content_type="application/json")
            if read_mode == "pending":
                pending_reads.append(route)
                return
            return route.continue_()
        body = route.request.post_data_json
        all_profile_posts.append(body)
        if "test" not in body: return route.continue_()
        posts.append(body)
        if mode == "reject": return route.fulfill(status=507, json={"error": "测试磁盘不可写"})
        if mode == "malformed": return route.fulfill(json={"test": {"est": body["test"]["est"]}})
        if mode == "pending":
            pending.append(route)
            return
        if mode == "lost":
            response = route.fetch()
            assert response.status == 200
            saved = json.loads((home / "profile.json").read_text(encoding="utf-8"))["test"]
            assert saved["est"] == body["test"]["est"]
            assert set(saved["unknown"]) == set(body["test"]["unknown"])
            if fail_read_after_write: fail_read = True
            return route.abort("failed")
        return route.continue_()

    def export_response(route):
        if fail_read: return route.fulfill(status=503, json={"error": "测试画像暂时不可读"})
        if export_override is not None: return route.fulfill(json=export_override)
        return route.continue_()

    def complete_test():
        page.locator('[data-act="start"]').click()
        for _ in range(25):
            rest = page.locator('[data-act="rest"]')
            if rest.count(): rest.click()
            button = page.locator('[data-act="next"]')
            if not button.count(): break
            button.click()
        expect(page.locator("[data-test-save]")).to_be_visible()

    def restore():
        current = page.request.get(base + "/api/profile", headers=headers).json()
        response = page.request.post(base + "/api/profile/import", headers=headers,
            data={"backup": original, "expected_rev": current["rev"]})
        assert response.status == 200

    page.route(base + "/api/profile", save_response)
    page.route(base + "/api/profile/export", export_response)
    try:
        before = (home / "profile.json").read_bytes()
        for read_mode in ("component", "malformed", "null"):
            page.goto(base + "/profile")
            expect(page.locator("#profileConnectionMessage")).to_contain_text("无法读取画像")
            expect(page.locator("#saveProfile")).to_be_disabled()
            expect(page.locator("#s1")).to_be_hidden()
            expect(page.locator("#s3")).to_be_hidden()
            expect(page.locator("#chooseBackup")).to_be_disabled()
            expect(page.locator("#clearProfile")).to_be_disabled()
            expect(page.locator("#recoveryPanel")).to_be_hidden()
            page.locator("#saveProfile").dispatch_event("click")
            assert (home / "profile.json").read_bytes() == before
            assert not all_profile_posts
        with page.expect_download() as download_info:
            page.locator("#exportProfile").click()
        backup_path = output / "profile-unavailable-backup.json"
        download_info.value.save_as(backup_path)
        assert json.loads(backup_path.read_text(encoding="utf-8"))["profile"] == original["profile"]
        page.screenshot(path=str(output / "profile-load-failure.png"), full_page=True)
        page.evaluate("""() => {
            window.originalReadTimeout = AbortSignal.timeout;
            AbortSignal.timeout = ms => originalReadTimeout(ms === 10000 ? 500 : ms);
        }""")
        read_mode = "pending"
        page.locator("#reloadProfile").click()
        expect(page.locator("#reloadProfile")).to_be_disabled()
        expect(page.locator("#profileConnectionMessage")).to_contain_text("正在读取")
        expect(page.locator("#profileConnectionMessage")).to_contain_text("请求超时")
        expect(page.locator("#reloadProfile")).to_be_enabled()
        assert (home / "profile.json").read_bytes() == before
        page.evaluate("AbortSignal.timeout = window.originalReadTimeout; delete window.originalReadTimeout")
        for route in pending_reads: route.abort("failed")
        pending_reads.clear()
        read_mode = None
        page.locator("#reloadProfile").click()
        expect(page.locator("#profileConnection")).to_be_hidden()
        expect(page.locator("#saveProfile")).to_be_enabled()
        expect(page.locator("#s1")).to_be_visible()
        expect(page.locator("#field")).to_have_value(original["profile"].get("field") or "")
        for key, value in original["profile"].get("exams", {}).items():
            assert float(page.locator("#ex-" + key).input_value()) == value
        assert not all_profile_posts and (home / "profile.json").read_bytes() == before
        complete_test()
        expect(page.locator("[data-test-save]")).to_contain_text("尚未确认保存")
        expect(page.locator("#test")).not_to_contain_text("结果已保存")
        assert (home / "profile.json").read_bytes() == before
        assert len(posts) == 1
        first_answers = posts[0]["test"]
        assert first_answers["unknown"]
        # An equal score with different answers is not confirmation of this attempt.
        export_override = {"profile": {"test": first_answers | {"unknown": []}}}
        page.locator('[data-act="check-save"]').click()
        expect(page.locator("[data-test-save]")).to_contain_text("尚未确认保存")
        expect(page.locator("#test")).to_contain_text("与本次作答不同")
        assert len(posts) == 1
        export_override = None
        mode = "malformed"
        page.locator('[data-act="retry-save"]').click()
        expect(page.locator("[data-test-save]")).to_contain_text("尚未确认保存")
        assert posts[-1]["test"] == first_answers
        assert (home / "profile.json").read_bytes() == before
        # A real deadline releases the UI and retains the same answers.
        page.evaluate("""() => {
            window.originalProfileTimeout = AbortSignal.timeout;
            AbortSignal.timeout = ms => originalProfileTimeout(ms === 10000 ? 500 : ms);
        }""")
        mode = "pending"
        page.locator('[data-act="retry-save"]').click()
        expect(page.locator('[data-act="retry-save"]')).to_be_disabled()
        expect(page.locator('[data-act="again"]')).to_be_disabled()
        page.locator('[data-act="retry-save"]').dispatch_event("click")
        expect(page.locator("[data-test-save]")).to_contain_text("尚未确认保存")
        assert len(posts) == 3 and posts[-1]["test"] == first_answers
        page.evaluate("AbortSignal.timeout = window.originalProfileTimeout; delete window.originalProfileTimeout")
        for route in pending: route.abort("failed")
        pending.clear()
        mode = "lost"
        fail_read_after_write = True
        page.locator('[data-act="retry-save"]').click()
        expect(page.locator("[data-test-save]")).to_contain_text("尚未确认保存")
        assert len(posts) == 4 and posts[-1]["test"] == first_answers
        assert json.loads((home / "profile.json").read_text(encoding="utf-8"))["test"]["est"] == first_answers["est"]
        page.screenshot(path=str(output / "vocabulary-save-unconfirmed.png"), full_page=True)
        fail_read = False
        fail_read_after_write = False
        page.locator('[data-act="check-save"]').click()
        expect(page.locator("[data-test-save]")).to_contain_text("已记录本次结果")
        expect(page.locator('[data-act="retry-save"]')).to_have_count(0)
        assert len(posts) == 4  # Checking never repeats the write.
        page.screenshot(path=str(output / "vocabulary-save-recovered.png"), full_page=True)
        # A second real test with a lost response is confirmed automatically by exact saved answers.
        restore()
        page.reload()
        complete_test()
        expect(page.locator("[data-test-save]")).to_contain_text("已记录本次结果")
        assert len(posts) == 5
        restore()
        mode = "success"
        page.reload()
        complete_test()
        expect(page.locator("[data-test-save]")).to_contain_text("已记录本次结果")
        assert len(posts) == 6
        assert not errors, errors
        print("Vocabulary test checks passed: rejection, malformed reply, timeout, lost response, retained answers and read-only confirmation.")
    finally:
        page.unroute(base + "/api/profile", save_response)
        page.unroute(base + "/api/profile/export", export_response)
        restore()
        page.close()


def check_profile_conflicts(context, base, home, output):
    from playwright.sync_api import expect

    first, second = context.new_page(), context.new_page()
    headers = {"X-Adaptive-Subs": "1"}
    original = first.request.get(base + "/api/profile/export", headers=headers).json()
    posts, errors = [], []
    for page in (first, second):
        page.on("pageerror", lambda error: errors.append(str(error)))
    second.on("request", lambda request: posts.append(request.post_data_json)
        if request.url == base + "/api/profile" and request.method == "POST" else None)

    def save_fields(body):
        current = first.request.get(base + "/api/profile", headers=headers).json()
        response = first.request.post(base + "/api/profile", headers=headers, data={**body, "expected_rev": current["rev"]})
        assert response.status == 200

    try:
        for page in (first, second):
            page.goto(base + "/profile")
            expect(page.locator("#saveProfile")).to_be_enabled()
        first.locator("#field").fill("第一个窗口")
        first.locator("#ex-cet4").fill("600")
        first.locator("#saveProfile").click()
        expect(first.locator("#msg1")).to_contain_text("已保存")
        before_stale = (home / "profile.json").read_bytes()
        second.locator("#field").fill("保留在旧窗口的填写")
        second.locator("#saveProfile").click()
        expect(second.locator("#msg1")).to_contain_text("当前填写已保留")
        expect(second.locator("#saveProfile")).to_be_disabled()
        expect(second.locator("#field")).to_have_value("保留在旧窗口的填写")
        second.locator("#saveProfile").dispatch_event("click")
        assert len(posts) == 1 and (home / "profile.json").read_bytes() == before_stale
        second.screenshot(path=str(output / "profile-form-conflict.png"), full_page=True)
        second.locator("#reloadProfileForm").click()
        expect(second.locator("#saveProfile")).to_be_enabled()
        expect(second.locator("#field")).to_have_value("第一个窗口")
        expect(second.locator("#ex-cet4")).to_have_value("600")
        second.locator("#field").fill("重新读取后保存")
        second.locator("#saveProfile").click()
        expect(second.locator("#msg1")).to_contain_text("已保存")
        assert json.loads((home / "profile.json").read_text(encoding="utf-8"))["exams"] == {"cet4": 600}

        # Refreshing the summary after clearing a mark must not bless old form fields with a new revision.
        assert first.request.post(base + "/api/mark", headers=headers,
            data={"key": "profileconflicttest", "state": "known"}).status == 200
        first.reload()
        expect(first.locator('[data-clear="profileconflicttest"]')).to_be_visible()
        save_fields({"field": "外部更新", "exams": {"cet4": 615}})
        first.locator('[data-clear="profileconflicttest"]').click()
        expect(first.locator('[data-clear="profileconflicttest"]')).to_be_hidden()
        expect(first.locator("#summary")).to_contain_text("615")
        expect(first.locator("#ex-cet4")).to_have_value("600")
        first.locator("#field").fill("概览刷新后的旧表单")
        first.locator("#saveProfile").click()
        expect(first.locator("#msg1")).to_contain_text("当前填写已保留")
        assert json.loads((home / "profile.json").read_text(encoding="utf-8"))["exams"] == {"cet4": 615}

        # A test started before another profile update is retained until the user explicitly retries.
        second.reload()
        second.locator('[data-act="start"]').click()
        save_fields({"field": "测试过程中另一个窗口的修改"})
        before_test = (home / "profile.json").read_bytes()
        before_posts = len(posts)
        for _ in range(25):
            rest = second.locator('[data-act="rest"]')
            if rest.count(): rest.click()
            button = second.locator('[data-act="next"]')
            if not button.count(): break
            button.click()
        expect(second.locator("[data-test-save]")).to_contain_text("尚未确认保存")
        expect(second.locator("#test")).to_contain_text("其他页面更新")
        assert (home / "profile.json").read_bytes() == before_test
        assert len(posts) == before_posts + 1
        answers = posts[-1]["test"]
        second.locator('[data-act="retry-save"]').click()
        expect(second.locator("[data-test-save]")).to_contain_text("已记录本次结果")
        assert len(posts) == before_posts + 2 and posts[-1]["test"] == answers
        saved = json.loads((home / "profile.json").read_text(encoding="utf-8"))
        assert saved["field"] == "测试过程中另一个窗口的修改" and saved["exams"] == {"cet4": 615}
        assert not errors, errors
        print("Profile conflict checks passed: stale forms, retained drafts, fresh reads, independent form revisions and test retry.")
    finally:
        current = first.request.get(base + "/api/profile", headers=headers).json()
        restored = first.request.post(base + "/api/profile/import", headers=headers,
            data={"backup": original, "expected_rev": current["rev"]})
        assert restored.status == 200
        first.close(); second.close()


def check_vocab_bank(context, base, home, output):
    from playwright.sync_api import expect

    page = context.new_page()
    errors, writes, pending = [], [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("request", lambda request: writes.append(request.url) if request.method == "POST" else None)
    before = (home / "profile.json").read_bytes()
    valid = json.loads((Path(__file__).parents[1] / "app/data/vocab_test.json").read_text(encoding="utf-8"))
    duplicate = json.loads(json.dumps(valid))
    duplicate["bands"][1]["words"][0]["word"] = duplicate["bands"][0]["words"][0]["word"]
    mode = "empty"
    def respond(route):
        if mode == "empty": return route.fulfill(json={"bands": [], "nonwords": valid["nonwords"]})
        if mode == "duplicate": return route.fulfill(json=duplicate)
        if mode == "json": return route.fulfill(body="{broken", content_type="application/json")
        if mode == "http": return route.fulfill(status=503, json={})
        if mode == "large": return route.fulfill(body="x" * (2 * 1024 * 1024 + 1), content_type="application/json")
        if mode == "pending":
            pending.append(route)
            return
        return route.continue_()
    page.route(base + "/data/vocab_test.json", respond)
    try:
        for mode, message in (("empty", "题库格式无效"), ("duplicate", "题库格式无效"),
                              ("json", "不是有效的 JSON"), ("http", "HTTP 503"), ("large", "题库文件过大")):
            page.goto(base + "/profile")
            expect(page.locator("#test")).to_contain_text(message)
            expect(page.locator('[data-act="start"]')).to_have_count(0)
            expect(page.locator('[data-act="reload-bank"]')).to_be_visible()
            assert not writes and (home / "profile.json").read_bytes() == before
        page.screenshot(path=str(output / "vocab-bank-error.png"), full_page=True)
        page.evaluate("""() => {
            window.originalBankTimeout = AbortSignal.timeout;
            AbortSignal.timeout = ms => originalBankTimeout(ms === 10000 ? 500 : ms);
        }""")
        mode = "pending"
        page.locator('[data-act="reload-bank"]').click()
        expect(page.locator("#test")).to_contain_text("正在加载")
        expect(page.locator('[data-act="reload-bank"]')).to_have_count(0)
        expect(page.locator("#test")).to_contain_text("题库读取超时")
        assert len(pending) == 1
        page.evaluate("AbortSignal.timeout = window.originalBankTimeout; delete window.originalBankTimeout")
        for route in pending: route.abort("failed")
        pending.clear()
        mode = "valid"
        page.locator('[data-act="reload-bank"]').click()
        expect(page.locator('[data-act="start"]')).to_be_visible()
        page.locator('[data-act="start"]').click()
        expect(page.locator(".titems .tw")).to_have_count(12)
        assert not errors and not writes and (home / "profile.json").read_bytes() == before
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        print("Vocabulary bank checks passed: invalid data, HTTP failure, size limit, timeout and successful reload without profile writes.")
    finally:
        page.close()
