"""Run the standalone trial in Chromium from a local Chinese Windows-compatible file path."""
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def check(table=None, *, expanded=False):
    with tempfile.TemporaryDirectory() as temp:
        folder = Path(temp) / "学习者 试测"
        folder.mkdir()
        if table is None:
            bank = json.loads((ROOT / "app/data/vocab_test.json").read_text(encoding="utf-8"))
            words = [item["word"] for band in bank["bands"] for item in band["words"]]
            if expanded:
                lines = (ROOT / "tools/cat/bank/contrast-sets.tsv").read_text(encoding="utf-8").splitlines()[1:]
                words.extend(cell.split("=", 1)[0] for line in lines for cell in line.split("\t")[1:])
                words = sorted(set(words), key=lambda w: hashlib.sha256(w.encode("utf-8")).digest())
            table = folder / "难度.json"
            table.write_text(json.dumps({"version": 1, "scale": "natural-log lemma rank",
                "words": {w: {"b_initial": i/50, "b_frequency": i/60, "rank": 300 + i * 17} for i, w in enumerate(words)}}), encoding="utf-8")
        page_file = folder / "试测.html"
        subprocess.run(["node", str(ROOT / "tools/build_cat_pilot.cjs"), str(table), str(page_file), *(["--expanded"] if expanded else [])], check=True)
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            context = browser.new_context(offline=True)
            page = context.new_page()
            errors, network = [], []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("request", lambda req: network.append(req.url) if req.url.startswith(("http://", "https://")) else None)
            page.goto(page_file.as_uri())
            page.locator("#prior").fill("100")
            page.locator("#prior").press("Enter")
            expected = []
            contrast_sets = set()
            items = {q["word"]: q for q in json.loads(page.locator("#data").text_content())["items"]}
            while page.locator("#quiz").is_visible():
                word = page.locator("#word").inner_text()
                assert word not in [r[0] for r in expected] and len(expected) < 40
                item = items[word]
                if item.get("contrastSet"):
                    assert item["contrastSet"] not in contrast_sets
                    contrast_sets.add(item["contrastSet"])
                    expect(page.locator("#context")).to_contain_text(item["context"])
                if len(expected) % 3 == 0:
                    expected.append((word, None))
                    page.keyboard.press("0")
                else:
                    choice = page.locator("#choices button").first
                    expected.append((word, choice.inner_text()))
                    if len(expected) % 2: choice.click()
                    else: page.keyboard.press("1")
            expect(page.locator("#done")).to_be_visible()
            results = {}
            for which in ("parameters", "record"):
                with page.expect_download() as d:
                    page.locator("#" + which).click()
                file = folder / d.value.suggested_filename
                d.value.save_as(file)
                results[which] = json.loads(file.read_text(encoding="utf-8"))
            params, record = results["parameters"], results["record"]
            assert params == record["parameters"]
            assert record["itemBank"]["count"] == len(items)
            assert record["itemBank"]["variant"] == ("expanded-v1" if expanded else "original")
            if expanded: assert contrast_sets and len(items) == 800
            assert params["tableSha256"] == hashlib.sha256(table.read_bytes()).hexdigest()
            assert 12 <= len(expected) <= 40
            assert [(a["word"], a["selected"]) for a in record["responses"]] == expected
            timing = record["timing"]
            assert 0 < timing["startedAt"] <= timing["completedAt"]
            assert 0 < timing["visibleMs"] <= timing["elapsedMs"]
            times = [r["elapsedMs"] for r in record["responses"]]
            assert all(isinstance(t, int) and t >= 0 for t in times)
            assert sum(times) <= timing["elapsedMs"] + len(times)  # rounding to whole milliseconds
            assert "timing" not in params
            assert not errors and not network, (errors, network)
            context.close()
            browser.close()
            print(f"Passed offline browser trial: {len(expected)} answers and both local exports.")
            return results


if __name__ == "__main__":
    import sys
    check(expanded="--expanded" in sys.argv[1:])
