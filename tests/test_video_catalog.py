import concurrent.futures
from contextlib import redirect_stdout
import io
import json
import multiprocessing
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import quote
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "app"), str(ROOT / "tools")]
import annotate
import build_site
import server
import subtitle_store
import video_catalog


def save(folder, ident, title):
    path = folder / (ident + ".json")
    data = {"video": {"id": ident, "title": title, "duration": 1}, "cues": []}
    subtitle_store.publish(path, data)
    return path


def write_title(folder, ident):
    annotate.update_index(folder, ident, "自定 " + ident, ident + ".json")


class CatalogTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name) / "视频"
        self.folder.mkdir()

    def test_salvages_valid_rows_orders_orphans_and_backs_up_damaged_source(self):
        for ident in ("clip", "orphan", "first"):
            save(self.folder, ident, "完整视频 " + ident)
        saved = [{"id": "first", "file": "first.json", "title": "旧标题", "custom": 8},
                 {"id": "bad", "file": "bad.json", "title": "残缺字幕"}, None]
        index = self.folder / "index.json"
        original = json.dumps(saved, ensure_ascii=False).encode("utf-8-sig")
        index.write_bytes(original)
        (self.folder / "bad.json").write_bytes(b'{"video":{"title":"bad"')
        self.assertEqual([r["id"] for r in video_catalog.read(self.folder)], ["first", "clip", "orphan"])
        self.assertEqual(index.read_bytes(), original)
        write_title(self.folder, "clip")
        rows = json.loads(index.read_bytes())
        self.assertEqual([r["id"] for r in rows], ["first", "orphan", "clip"])
        self.assertEqual(rows[0], saved[0])
        backup, = (self.folder / "recovery").iterdir()
        self.assertEqual(backup.read_bytes(), original)
        with self.assertRaises(ValueError):
            write_title(self.folder, "missing")
        self.assertEqual(json.loads(index.read_bytes()), rows)

    def test_spawned_processes_preserve_each_others_custom_titles(self):
        ids = [f"clip-{i}" for i in range(12)]
        for ident in ids:
            save(self.folder, ident, ident)
        with concurrent.futures.ProcessPoolExecutor(max_workers=4, mp_context=multiprocessing.get_context("spawn")) as pool:
            futures = [pool.submit(write_title, self.folder, ident) for ident in ids]
            for future in futures:
                future.result(timeout=60)
        rows = json.loads((self.folder / "index.json").read_bytes())
        self.assertCountEqual([(r["id"], r["title"]) for r in rows], [(i, "自定 " + i) for i in ids])

    def test_http_recent_and_site_agree_with_actual_playable_unicode_files(self):
        ident = "课程 #1"
        title = "中文标题" * 600
        path = save(self.folder, ident, title)
        (self.folder / "index.json").write_bytes(b"[")
        (self.folder / "wrong.json").write_bytes(path.read_bytes())
        (self.folder / "missing.json").write_text('{"video":{"title":"partial"}', encoding="utf-8")
        (self.folder / "demo.dry-run.json").write_bytes(path.read_bytes())
        (self.folder / "vocab_test.json").write_bytes(b'{"questions":[]}')
        with patch.multiple(server, DATA=self.folder, EXT_DATA=self.folder / "ext"):
            httpd = server.Server(("127.0.0.1", 0), server.Handler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{httpd.server_port}/data/"
                with urlopen(base + "index.json", timeout=5) as reply:
                    index = json.load(reply)
                self.assertEqual(index, [{"id": ident, "title": title, "file": path.name}])
                with urlopen(base + quote(path.name), timeout=5) as reply:
                    self.assertEqual(json.load(reply)["video"]["id"], ident)
                self.assertEqual([row["title"] for row in server.recent_videos()], [title])
                site = self.folder.parent / "site"
                with patch.multiple(build_site, VIDEOS=self.folder, SITE=site), redirect_stdout(io.StringIO()):
                    build_site.main()
                self.assertEqual(json.loads((site / "data" / "index.json").read_bytes()), index)
                self.assertEqual((site / "data" / path.name).read_bytes(), path.read_bytes())
                self.assertCountEqual([p.name for p in (site / "data").iterdir()], ["vocab_test.json", "index.json", path.name])
                path.write_bytes(b"{")
                self.assertEqual(server.recent_videos(), [])
            finally:
                httpd.shutdown()
                thread.join(5)
                httpd.server_close()


if __name__ == "__main__":
    unittest.main()
