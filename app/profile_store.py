"""The learner's profile and known-word library, shared by the player and the browser extension.

Profile (saved as profile.json next to the program's config):
  identity / stage / field     who the learner is (student: high school / undergrad / grad; or working)
  exams                        exam scores, e.g. {"cet4": 560, "ielts": 6.5}
  level                        estimated vocabulary size (vocab test, else guessed from exams / identity)
  test                         last vocab test: estimate, date, words answered known / unknown
  known / unknown              words the learner marked while watching (always wins)

Known-word library (what is never glossed as a single word):
  base   = words on the exam lists the learner's scores say they have mastered (ECDICT tags)
         + the most frequent words, conservatively (rank <= 0.6 x level)
  then   + words answered "认识" in the test,  - words answered "不认识"
  then   + words marked "我认识",               - words marked "我不认识"   (marks override everything)
Phrases and common words in an uncommon sense are never hidden by the library, only by their own mark
("phrase:…" / "sense:…" keys), so a known word used in a special meaning is still explained.
"""

from __future__ import annotations

import json
import re
import threading
import time
from functools import lru_cache
from pathlib import Path

import annotate as A
from storage import write_json

EXAM_FILE = Path(__file__).parent / "data" / "exam_words.json"     # built by tools/make_exam_words.py (ECDICT)
TAGS = ["zk", "gk", "cet4", "cet6", "ky", "ielts", "toefl", "gre"]
TAG_NAMES = {"zk": "中考", "gk": "高考", "cet4": "四级", "cet6": "六级", "ky": "考研",
             "ielts": "雅思", "toefl": "托福", "gre": "GRE"}
EXAMS = {   # id: (name, min, max)
    "gaokao": ("高考英语", 0, 150), "cet4": ("大学英语四级", 0, 710), "cet6": ("大学英语六级", 0, 710),
    "kaoyan": ("考研英语", 0, 100), "ielts": ("雅思", 0, 9), "toefl": ("托福", 0, 120),
}
IDENTITIES = {"high": "高中生", "undergrad": "大学生", "grad": "研究生", "work": "已工作", "other": "其他"}
KEY_RE = re.compile(r"[a-z][a-z' .:-]{0,79}")
LEVELS = [1500, 2000, 2500, 3000, 4000, 5000, 6000, 7000, 8000, 10000, 12000, 15000]


# ------------------------------------------------------ exams -> lists ---

def _exam_rule(exam: str, score: float) -> tuple[set[str], int]:
    """(word lists mastered, rough vocabulary size) for one exam score."""
    if exam == "gaokao":
        if score >= 135: return {"zk", "gk"}, 4500
        if score >= 120: return {"zk", "gk"}, 3500
        if score >= 90:  return {"zk"}, 2500
        return set(), 1500
    if exam == "cet4":
        if score >= 600: return {"zk", "gk", "cet4"}, 5000
        if score >= 550: return {"zk", "gk", "cet4"}, 4500
        if score >= 425: return {"zk", "gk"}, 3500
        return {"zk"}, 2500
    if exam == "cet6":
        if score >= 600: return {"zk", "gk", "cet4", "cet6"}, 7000
        if score >= 550: return {"zk", "gk", "cet4", "cet6"}, 6000
        if score >= 425: return {"zk", "gk", "cet4"}, 5000
        return {"zk", "gk"}, 4000
    if exam == "kaoyan":
        if score >= 75: return {"zk", "gk", "cet4", "cet6", "ky"}, 6000
        if score >= 60: return {"zk", "gk", "cet4"}, 5000
        return {"zk", "gk"}, 4000
    if exam == "ielts":
        if score >= 8:   return {"zk", "gk", "cet4", "cet6", "ielts"}, 9000
        if score >= 7:   return {"zk", "gk", "cet4", "cet6", "ielts"}, 7000
        if score >= 6.5: return {"zk", "gk", "cet4", "cet6"}, 6000
        if score >= 6:   return {"zk", "gk", "cet4"}, 5000
        if score >= 5.5: return {"zk", "gk", "cet4"}, 4500
        return {"zk", "gk"}, 3500
    if exam == "toefl":
        if score >= 110: return {"zk", "gk", "cet4", "cet6", "toefl"}, 9000
        if score >= 100: return {"zk", "gk", "cet4", "cet6", "toefl"}, 7000
        if score >= 90:  return {"zk", "gk", "cet4", "cet6"}, 6000
        if score >= 80:  return {"zk", "gk", "cet4"}, 5000
        return {"zk", "gk"}, 4000
    return set(), 0


# no exam scores given: what someone at this stage has usually studied
IDENTITY_RULE = {"high": ({"zk"}, 2500), "undergrad": ({"zk", "gk"}, 3500), "grad": ({"zk", "gk", "cet4"}, 5000),
                 "work": ({"zk", "gk"}, 3500), "other": (set(), 3000)}


def mastered_lists(p: dict) -> tuple[set[str], int]:
    """Union of the lists from all exam scores (or the identity default), and the prior vocabulary size."""
    tags, prior = set(), 0
    for exam, score in (p.get("exams") or {}).items():
        t, lv = _exam_rule(exam, score)
        tags |= t
        prior = max(prior, lv)
    if not prior:
        tags, prior = IDENTITY_RULE.get(p.get("identity") or "other", IDENTITY_RULE["other"])
        tags = set(tags)
    return tags, prior


@lru_cache(maxsize=1)
def exam_words() -> dict[str, list]:
    if not EXAM_FILE.exists():
        return {}
    return json.loads(EXAM_FILE.read_text(encoding="utf-8")).get("words", {})


def define(word: str) -> dict | None:
    w = word.lower().strip()
    hit = exam_words().get(w)
    if not hit:
        return None
    mask, zh = hit
    return {"word": w, "zh": zh, "short": first_sense(zh),
            "lists": [TAG_NAMES[t] for i, t in enumerate(TAGS) if mask >> i & 1]}


def first_sense(zh: str) -> str:
    """'vt. 放弃, 遗弃 n. 放任' -> '放弃' (short enough to sit above a word in the subtitle)."""
    s = re.sub(r"^\s*([a-z]+\.\s*)+", "", zh)
    s = re.split(r"[,，;；]|\s[a-z]+\.\s", s)[0]
    s = re.sub(r"[（(][^）)]*[）)]", "", s).strip()
    return s[:10]


def base_known(tags: set[str], level: int) -> tuple[set[str], dict]:
    """Known-word base from mastered exam lists + the most frequent words."""
    ranks = A.load_ranks()
    big = 10**9
    mask = sum(1 << i for i, t in enumerate(TAGS) if t in tags)
    from_exams = set()
    if mask:
        cap = max(3 * level, 6000)        # an exam word far rarer than the learner's level: don't assume it
        for w, (m, _zh) in exam_words().items():
            if m & mask and ranks.get(w, big) <= cap:
                from_exams.add(w)
    freq_cut = int(0.6 * level)           # conservative: a vocabulary size is not "exactly the top N words"
    from_freq = {w for w, r in ranks.items() if r <= freq_cut and w.isalpha()}
    return from_exams | from_freq, {"exams": len(from_exams), "frequent": len(from_freq), "freq_cut": freq_cut}


# ------------------------------------------------------------- storage ---

class Store:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        self._cache = None        # (rev, payload) of the computed library

    def load(self) -> dict:
        try:
            p = json.loads(self.path.read_text(encoding="utf-8"))
            return p if isinstance(p, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, p: dict):
        p["rev"] = int(p.get("rev", 0)) + 1
        p["updated"] = int(time.time())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_json(self.path, p)
        self._cache = None

    # -- what clients get --
    def payload(self) -> dict:
        with self.lock:
            p = self.load()
            rev = p.get("rev", 0)
            if self._cache and self._cache[0] == rev:
                return self._cache[1]
            tags, prior = mastered_lists(p)
            level = int(p.get("level") or prior)
            base, stats = base_known(tags, level)
            test = p.get("test") or {}
            t_known, t_unknown = set(test.get("known") or []), set(test.get("unknown") or [])
            m_known, m_unknown = set(p.get("known") or []), set(p.get("unknown") or [])
            unknown = m_unknown | (t_unknown - m_known)
            known = ((base | t_known) - unknown) | m_known
            out = {
                "rev": rev,
                "setup": bool(p.get("identity") or p.get("exams") or p.get("test")),
                "profile": {k: p.get(k) for k in ("identity", "field", "exams")},
                "level": level, "prior": prior,
                "test": {k: test.get(k) for k in ("est", "date", "bands")} if test else None,
                "lists": [TAG_NAMES[t] for t in TAGS if t in tags],
                "stats": {**stats, "known": len(known), "unknown": len(unknown),
                          "marked_known": len(m_known), "marked_unknown": len(m_unknown)},
                "known": sorted(known), "unknown": sorted(unknown),
                "marks": {"known": sorted(m_known), "unknown": sorted(m_unknown)},
            }
            self._cache = (rev, out)
            return out

    # -- updates --
    def update(self, body: dict) -> str | None:
        """Profile fields from the setup page. Returns an error message or None."""
        with self.lock:
            p = self.load()
            if "identity" in body:
                ident = body.get("identity")
                if ident not in IDENTITIES and ident not in (None, ""):
                    return "身份选项不对"
                p["identity"] = ident or None
            if "field" in body:
                p["field"] = str(body.get("field") or "").strip()[:40] or None
            if "exams" in body:
                exams = {}
                for k, v in (body.get("exams") or {}).items():
                    if k not in EXAMS or v in (None, ""):
                        continue
                    try:
                        v = float(v)
                    except (TypeError, ValueError):
                        return f"{EXAMS[k][0]}的分数不是数字"
                    lo, hi = EXAMS[k][1], EXAMS[k][2]
                    if not lo <= v <= hi:
                        return f"{EXAMS[k][0]}的分数应该在 {lo}–{hi} 之间"
                    exams[k] = v
                p["exams"] = exams
            if "level" in body:
                try:
                    p["level"] = min(LEVELS, key=lambda lv: abs(lv - int(body["level"])))
                except (TypeError, ValueError):
                    return "词汇量不是数字"
            if ("exams" in body or "identity" in body) and "level" not in body and not p.get("test"):
                p.pop("level", None)      # no test yet: the level follows the exams / identity
            if isinstance(body.get("test"), dict):
                t = body["test"]
                try:
                    est = int(t.get("est"))
                except (TypeError, ValueError):
                    return "测试结果不完整"
                clean = lambda xs: sorted({str(x).lower() for x in (xs or []) if KEY_RE.fullmatch(str(x).lower())})[:500]
                bands = [round(float(b), 3) for b in (t.get("bands") or [])][:20]
                p["test"] = {"est": est, "date": time.strftime("%Y-%m-%d"), "bands": bands,
                             "known": clean(t.get("known")), "unknown": clean(t.get("unknown"))}
                p["level"] = min(LEVELS, key=lambda lv: abs(lv - est))
            self._save(p)
            return None

    def mark(self, key: str, state: str) -> str | None:
        key = str(key or "").lower().strip()
        if not KEY_RE.fullmatch(key) or state not in ("known", "unknown", "clear"):
            return "bad mark"
        with self.lock:
            p = self.load()
            known, unknown = set(p.get("known") or []), set(p.get("unknown") or [])
            known.discard(key)
            unknown.discard(key)
            if state == "known":
                known.add(key)
            elif state == "unknown":
                unknown.add(key)
            p["known"], p["unknown"] = sorted(known), sorted(unknown)
            self._save(p)
            return None

    def reset_marks(self):
        with self.lock:
            p = self.load()
            p["known"], p["unknown"] = [], []
            self._save(p)
