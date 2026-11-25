import concurrent.futures
from contextlib import ExitStack
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import annotate
import server
import subtitle_store as store
import storage

VID = "abcdefghijk"


def example(vid=VID):
    return {"video": {"id": vid, "title": "字幕样例", "duration": 4},
            "cues": [{"s": 0, "e": 4, "text": "😀 bank", "notes": [
                {"s": 2,"e": 6,"zh": "河岸","key": "bank","kind": "sense","lv": 6000}]}]}


class SubtitleStoreTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name) / "视频缓存"
        self.folder.mkdir()
        self.path = self.folder / (VID + ".json")

    def test_complete_legacy_bom_and_codepoint_offsets_round_trip(self):
        self.path.write_text(json.dumps(example(), ensure_ascii=False), encoding="utf-8-sig")
        self.assertEqual(store.snapshot(self.path, VID).data, example())
        self.assertIsNone(store.snapshot(self.path, "other"))
        self.assertTrue(store.publish(self.path, example()))
        self.assertEqual(json.loads(self.path.read_bytes()), example())

    def test_incomplete_or_invalid_results_do_not_overwrite_ready_file(self):
        store.publish(self.path, example())
        original = self.path.read_bytes()
        bad = example()
        bad["cues"][0]["notes"][0]["e"] = 9
        for data in (example() | {"status":"partial"}, example("other"), bad):
            with self.assertRaises(ValueError): store.publish(self.path,data)
        with patch.object(storage.os,"replace",side_effect=PermissionError("locked")):
            with self.assertRaises(PermissionError): store.publish(self.path,example())
        self.assertEqual(self.path.read_bytes(),original)
        self.assertFalse(list(self.folder.glob("*.tmp")))

    def test_parallel_repairs_can_only_publish_the_snapshot_once(self):
        store.publish(self.path, example())
        original = store.snapshot(self.path,VID)
        gate = threading.Barrier(2)
        def repair(i):
            revised = {**original.data, "repair": i}
            gate.wait(timeout=10)
            return store.publish(self.path,revised,expected_digest=original.digest)
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            self.assertCountEqual(pool.map(repair,[1,2]),[True,False])
        self.path.unlink()
        self.assertFalse(store.publish(self.path,example(),expected_digest=original.digest))
        self.assertFalse(self.path.exists())

    def test_http_open_static_probe_and_asr_fallback_use_validated_results(self):
        ext = self.folder / "ext"
        with ExitStack() as st:
            st.enter_context(patch.multiple(server,DATA=self.folder,EXT_DATA=ext))
            st.enter_context(patch.object(server.P,"has_key",return_value=False))
            new_job = st.enter_context(patch.object(server,"get_job",side_effect=AssertionError("unrequested generation")))
            httpd = server.Server(("127.0.0.1",0),server.Handler)
            st.callback(httpd.server_close)
            thread = threading.Thread(target=httpd.serve_forever,daemon=True)
            thread.start(); st.callback(thread.join,5); st.callback(httpd.shutdown)
            base = f"http://127.0.0.1:{httpd.server_port}"
            def call(path,body=None):
                raw = json.dumps(body).encode() if body is not None else None
                req = Request(base+path,data=raw,headers={"X-Adaptive-Subs":"1","Content-Type":"application/json"})
                try: reply = urlopen(req,timeout=5)
                except HTTPError as error: reply=error
                with reply: return reply.status,json.loads(reply.read())
            self.path.write_bytes(b'{"video":')
            self.assertEqual(call("/data/"+VID+".json")[0],409)
            self.assertEqual(call("/api/open?url="+VID)[0],400)
            asr = "yt_"+VID+"_asr"
            store.publish(ext/(asr+".json"),example(asr))
            code, fallback = call("/api/ext/open",{"id":VID,"source":"youtube","probe":True})
            self.assertEqual((code,fallback["id"],fallback["cached"]),(200,asr,True))
            store.publish(self.path,example())
            self.assertEqual(call("/data/"+VID+".json"),(200,example()))
            self.assertTrue(call("/api/open?url="+VID)[1]["cached"])
            with patch.object(server.SS,"snapshot",side_effect=PermissionError):
                self.assertEqual(call("/api/open?url="+VID)[0],503)
            new_job.assert_not_called()

    def test_dry_run_leaves_existing_paid_subtitles_and_index_intact(self):
        source = self.folder / (VID+".en.srt")
        source.write_text("1\n00:00:00,000 --> 00:00:04,000\nA test.\n", encoding="utf-8-sig")
        store.publish(self.path,example())
        original = self.path.read_bytes()
        with patch.object(sys,"argv",["annotate.py",str(source),"--dry-run"]), patch.object(annotate,"load_dotenv"), \
             patch.object(annotate,"find_candidates",return_value=[[]]), patch.object(annotate,"add_keys"):
            annotate.main()
        self.assertEqual(self.path.read_bytes(),original)
        self.assertEqual(json.loads((self.folder/(VID+".dry-run.json")).read_bytes())["status"],"preview")
        self.assertFalse((self.folder/"index.json").exists())


if __name__ == "__main__":
    unittest.main()
