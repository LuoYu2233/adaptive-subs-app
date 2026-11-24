"""Exercise real vocabulary-test completion and uncertain save recovery without model calls."""
import json


def check_profile(context, base, home, output):
    from playwright.sync_api import expect

    page = context.new_page()
    errors, posts, pending = [], [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    headers = {"X-Adaptive-Subs": "1"}
    original = page.request.get(base + "/api/profile/export", headers=headers).json()
    mode = "reject"
    fail_read = False
    export_override = None

    def save_response(route):
        if route.request.method != "POST": return route.continue_()
        body = route.request.post_data_json
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
            saved = json.loads((home / "profile.json").read_text())["test"]
            assert saved["est"] == body["test"]["est"]
            assert set(saved["unknown"]) == set(body["test"]["unknown"])
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
        page.goto(base + "/profile")
        before = (home / "profile.json").read_bytes()
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
        fail_read = True
        page.locator('[data-act="retry-save"]').click()
        expect(page.locator("[data-test-save]")).to_contain_text("尚未确认保存")
        assert len(posts) == 4 and posts[-1]["test"] == first_answers
        assert json.loads((home / "profile.json").read_text())["test"]["est"] == first_answers["est"]
        page.screenshot(path=str(output / "vocabulary-save-unconfirmed.png"), full_page=True)
        fail_read = False
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
