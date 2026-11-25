from hashlib import sha256
from html.parser import HTMLParser
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import build_trial_materials as materials


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.targets = []

    def handle_starttag(self, tag, attrs):
        if tag == "a": self.targets.append(dict(attrs)["href"])


class TrialMaterialsTests(unittest.TestCase):
    def test_real_file_names_links_fingerprint_and_atomic_rebuild(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "短语与单词"
            folder.mkdir()
            table = folder / "难度.json"
            table.write_text(json.dumps({"version":1,"scale":"natural-log lemma rank","words":{"bank":{}}}),encoding="utf-8-sig")
            word = folder / "单词输入.html"
            phrase = folder / "短语输入.html"
            def page(path, data):
                path.write_text('<script id="data" type="application/json">'+json.dumps(data)+'</script>',encoding="utf-8")
            page(word, {"items":[{"word":"bank"}],"tableSha256":sha256(table.read_bytes()).hexdigest()})
            page(phrase, {"items":[{"phrase":"take off","level":2}],"bankSha256":"a"*64})
            private = folder / "个人测量.json"
            private.write_text("私人结果",encoding="utf-8")
            out = folder / "共享资料.zip"
            self.assertEqual(materials.build(word,phrase,table,out)["words"],1)
            original = out.read_bytes()
            with ZipFile(out) as z:
                self.assertIsNone(z.testzip())
                self.assertCountEqual(z.namelist(),["先打开这里.html","单词能力试测.html","短语能力试测.html","word-difficulty-v1.json","来源与许可.txt"])
                guide = z.read("先打开这里.html").decode("utf-8")
                self.assertIn("AdaptiveSubs-Calibration.exe",guide)
                links = Links(); links.feed(guide)
                for target in links.targets: self.assertIn(target,z.namelist())
                self.assertEqual(z.read("word-difficulty-v1.json"),table.read_bytes())
                self.assertIn("CC BY-SA 4.0",z.read("来源与许可.txt").decode("utf-8-sig"))
            materials.build(word,phrase,table,out)
            self.assertEqual(out.read_bytes(),original)
            with patch.object(materials.os,"replace",side_effect=PermissionError):
                with self.assertRaises(PermissionError): materials.build(word,phrase,table,out)
            self.assertFalse(list(folder.glob("*.tmp")))
            table.write_bytes(table.read_bytes() + b" ")
            with self.assertRaisesRegex(ValueError,"不匹配"): materials.build(word,phrase,table,out)
            self.assertEqual(out.read_bytes(),original)


if __name__ == "__main__":
    unittest.main()
