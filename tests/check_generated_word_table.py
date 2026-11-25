"""Check the real table used by the pilot and print its fingerprint for comparison between OS builds."""
from hashlib import sha256
import json
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    raw = (root / "build/word-difficulty-v1.json").read_bytes()
    table = json.loads(raw)
    assert table["version"] == 1 and table["scale"] == "natural-log lemma rank"
    assert len(table["words"]) == 30000 and table["words"]["go"]["forms"] > 1
    assert "saw" in table["words"] and table["coverage"]["ambiguous"] > 0
    assert table["sources"]["wordfreq"] == "3.1.1"
    bank = json.loads((root / "app/data/vocab_test.json").read_text(encoding="utf-8"))
    real_items = {item["word"] for band in bank["bands"] for item in band["words"]}
    missing = real_items - table["words"].keys()
    assert not missing, f"Items without a matching difficulty: {sorted(missing)}"
    print(sha256(raw).hexdigest())


if __name__ == "__main__":
    main()
