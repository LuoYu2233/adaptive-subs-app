"""Build data/exam_words.json from ECDICT (https://github.com/skywind3000/ECDICT, MIT License).

    python make_exam_words.py path/to/ecdict.csv

For every common English word it keeps:
  - which Chinese exam word lists it belongs to (中考 / 高考 / 四级 / 六级 / 考研 / 雅思 / 托福 / GRE),
    as a bit mask — used to build the learner's known-word base from their exam scores
  - a short Chinese meaning — shown when the viewer clicks a word that has no annotation
Output: {"tags": [...], "words": {word: [mask, "中文"]}}
"""

import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent
TAGS = ["zk", "gk", "cet4", "cet6", "ky", "ielts", "toefl", "gre"]   # ECDICT's tag names, in this bit order
MAX_RANK = 10**9           # also keep every untagged word that has a corpus frequency rank (for meanings)
WORD_RE = re.compile(r"[a-z][a-z-]*[a-z]")


def short_zh(translation: str) -> str:
    """First one or two senses, without web glosses or long notes."""
    lines = [ln.strip() for ln in translation.replace("\\n", "\n").splitlines()]
    # drop web glosses ("[网络] …"), keep subject-area senses ("[计] 衬线" -> "衬线")
    lines = [re.sub(r"^\[[^\]]{1,4}\]\s*", "", ln) for ln in lines if ln and not ln.startswith("[网络]")]
    lines = [ln for ln in lines if ln]
    if not lines:
        return ""
    s = " ".join(lines[:2])
    s = re.sub(r"\s+", " ", s)
    return s[:60]


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    csv.field_size_limit(10_000_000)
    out = {}
    with open(sys.argv[1], encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            w = row["word"].strip()
            if not WORD_RE.fullmatch(w):       # lowercase words only: no names, abbreviations, phrases
                continue
            tags = set(row["tag"].split())
            mask = sum(1 << i for i, t in enumerate(TAGS) if t in tags)
            ranks = [int(x) for x in (row["frq"], row["bnc"]) if x and x.isdigit() and int(x) > 0]
            common = bool(ranks) and min(ranks) <= MAX_RANK
            if not mask and not common:
                continue
            zh = short_zh(row["translation"])
            if not zh and not mask:
                continue
            out[w] = [mask, zh]
    dest = ROOT / "data" / "exam_words.json"
    dest.write_text(json.dumps({"tags": TAGS, "source": "ECDICT (MIT License)", "words": out},
                               ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    counts = {t: sum(1 for m, _ in out.values() if m >> i & 1) for i, t in enumerate(TAGS)}
    print(f"{len(out)} words -> {dest} ({dest.stat().st_size / 1024:.0f} KB)")
    print("per list:", counts)


if __name__ == "__main__":
    main()
