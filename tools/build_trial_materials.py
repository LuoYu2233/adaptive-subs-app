"""Package matching offline tests and difficulty data for Windows users."""
import argparse
from hashlib import sha256
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]


class EmbeddedData(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.active = tag == "script" and attrs.get("id") == "data" and attrs.get("type") == "application/json"

    def handle_endtag(self, tag):
        if tag == "script":
            self.active = False

    def handle_data(self, text):
        if self.active:
            self.parts.append(text)


def embedded(page):
    parser = EmbeddedData()
    parser.feed(page.decode("utf-8-sig"))
    parser.close()
    if len(parser.parts) != 1:
        raise ValueError("试测页面缺少唯一的内嵌题库")
    data = json.loads(parser.parts[0])
    if not isinstance(data, dict) or not isinstance(data.get("items"), list) or not data["items"]:
        raise ValueError("试测题库无效")
    return data


def build(word_page, phrase_page, table_file, output):
    inputs = [Path(p) for p in (word_page, phrase_page, table_file)]
    output = Path(output)
    if output.resolve() in [p.resolve() for p in inputs]:
        raise ValueError("输出 ZIP 不能覆盖输入文件")
    word, phrase, table = [p.read_bytes() for p in inputs]
    difficulty = json.loads(table.decode("utf-8-sig"))
    measured, phrases = embedded(word), embedded(phrase)
    digest = sha256(table).hexdigest()
    if (not isinstance(difficulty, dict) or difficulty.get("version") != 1 or difficulty.get("scale") != "natural-log lemma rank"
            or not isinstance(difficulty.get("words"), dict) or not difficulty["words"]
            or measured.get("tableSha256") != digest):
        raise ValueError("单词试测页面与难度表不匹配")
    if not all(isinstance(r, dict) and r.get("word") in difficulty["words"] for r in measured["items"]):
        raise ValueError("单词题库与难度表词元不匹配")
    phrase_hash = phrases.get("bankSha256", "")
    if (not isinstance(phrase_hash, str) or len(phrase_hash) != 64 or any(c not in "0123456789abcdef" for c in phrase_hash)
            or not all(isinstance(r, dict) and isinstance(r.get("phrase"), str) and type(r.get("level")) is int and 1 <= r["level"] <= 5 for r in phrases["items"])):
        raise ValueError("需要独立短语试测页面")
    package = ROOT / "packaging/trial"
    guide = (package / "start.html").read_text(encoding="utf-8").replace("@@WORDS@@", str(len(measured["items"]))).replace(
        "@@PHRASES@@", str(len(phrases["items"])))
    files = {"先打开这里.html": guide.encode("utf-8"), "单词能力试测.html": word, "短语能力试测.html": phrase,
             "word-difficulty-v1.json": table, "来源与许可.txt": (package / "third-party-notices.txt").read_text(encoding="utf-8").encode("utf-8-sig")}
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=output.parent, prefix="." + output.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w+b") as stream:
            with ZipFile(stream, "w") as archive:
                for name, content in sorted(files.items()):
                    entry = ZipInfo(name)
                    entry.create_system = 3
                    entry.external_attr = 0o100644 << 16
                    archive.writestr(entry, content, compress_type=ZIP_DEFLATED, compresslevel=9)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, output)
    finally:
        Path(temp).unlink(missing_ok=True)
    return {"words": len(measured["items"]), "phrases": len(phrases["items"]), "tableSha256": digest}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    for name in ("word_page", "phrase_page", "table_file", "output"):
        ap.add_argument(name, type=Path)
    args = ap.parse_args()
    print(json.dumps(build(**vars(args))))
