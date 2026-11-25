"""Adaptive subtitles: generate level-tagged annotations for a subtitle file.

One run produces annotations for learners of ALL levels. Each annotation carries a
difficulty "level" (vocabulary size at which a Chinese learner knows it in this meaning);
the web player filters by the viewer's own level in real time.

    python annotate.py data/ID.en.srt --title "Video title"   # full run (needs OPENAI_API_KEY)
    python annotate.py data/ID.en.srt --dry-run               # frequency step only, no API
    python annotate.py data/ID.en.srt --export-srt 5000       # .srt for one level, from the JSON

Pipeline:
  1. spaCy over the whole transcript (sentences that span cues keep their context)
  2. wordfreq rank of each content word; rank > MIN_RANK -> candidate
  3. LLM: keep/drop candidates, add idioms / phrasal verbs / uncommon senses,
     short Chinese gloss for this context, difficulty level per item
  4. LLM: natural Chinese translation of every line (for the bilingual comparison mode)
  5. post-process: locate spans, normalise glosses, lemma keys, one gloss per key
Output: data/ID.json (read by player.html)
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import threading
import time

import srt

import subtitle_store as SS
from storage import write_json

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).parent          # app/: data/ next to the code
PROJECT = ROOT.parent                 # repository root: .env and the API cache when run from source
CACHE_DIR = PROJECT / ".cache"
MIN_RANK = 1500
LEVELS = [1500, 2000, 2500, 3000, 4000, 5000, 6000, 8000, 10000, 12000, 15000, 20000]

SKIP_ENTS = {
    "PERSON", "ORG", "GPE", "LOC", "NORP", "FAC", "DATE", "TIME",
    "MONEY", "PERCENT", "QUANTITY", "ORDINAL", "CARDINAL", "PRODUCT", "EVENT", "WORK_OF_ART",
}
CONTENT_POS = {"NOUN", "VERB", "ADJ", "ADV"}

_nlp = None


def nlp():
    global _nlp
    if _nlp is None:
        import spacy
        _nlp = spacy.load("en_core_web_sm")
    return _nlp


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def snap_level(x) -> int:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return 5000
    return min(LEVELS, key=lambda lv: abs(lv - x))


# ---------------------------------------------------------- candidate step ---

RANKS_FILE = Path(__file__).parent / "data" / "ranks_en.txt"   # wordfreq's English top 100k, one word per line


@functools.lru_cache(maxsize=2)
def load_ranks(n: int = 100_000) -> dict[str, int]:
    if RANKS_FILE.exists():           # the packaged program ships this instead of wordfreq (all languages, 58 MB)
        words = RANKS_FILE.read_text(encoding="utf-8").splitlines()[:n]
    else:
        from wordfreq import top_n_list
        words = top_n_list("en", n)
    return {w: i + 1 for i, w in enumerate(words) if w}


def find_candidates(texts: list[str]) -> list[list[dict]]:
    """For each cue: [{word, lemma, rank}] content words rarer than MIN_RANK."""
    ranks = load_ranks()
    big = 10**9
    starts, pos = [], 0
    for t in texts:
        starts.append(pos)
        pos += len(t) + 1
    doc = nlp()(" ".join(texts))

    def cue_of(char_idx: int) -> int:
        lo, hi = 0, len(starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if starts[mid] <= char_idx:
                lo = mid
            else:
                hi = mid - 1
        return lo

    # sound tags like "[laughter]" / "[snorts]" are not words to learn
    tags = [m.span() for m in re.finditer(r"\[[^\]]*\]", doc.text)]

    out: list[list[dict]] = [[] for _ in texts]
    seen: list[set[str]] = [set() for _ in texts]
    for tok in doc:
        if not tok.is_alpha or tok.is_stop or len(tok.text) < 3:
            continue
        if any(a <= tok.idx < b for a, b in tags):
            continue
        if tok.ent_type_ in SKIP_ENTS or tok.pos_ == "PROPN" or tok.pos_ not in CONTENT_POS:
            continue
        lemma = tok.lemma_.lower()
        r = min(ranks.get(lemma, big), ranks.get(tok.text.lower(), big))
        if r <= MIN_RANK:
            continue
        i = cue_of(tok.idx)
        if lemma in seen[i]:
            continue
        seen[i].add(lemma)
        out[i].append({"word": tok.text, "lemma": lemma, "rank": r if r < big else 50000})
    return out


# ---------------------------------------------------------------- LLM step ---

ANNOTATE_PROMPT = """You annotate English video subtitles for Chinese learners of English at ALL levels.
A web player will show each annotation only to learners whose vocabulary is smaller than the
annotation's "level" — so you estimate difficulty; you do not decide for one learner.

Input: JSON list of lines: id, prev, line, next, and "candidates" — words a frequency list flags as
non-basic, each with its frequency rank (1 = most common English word).

Recall matters more than precision: a learner seeing a gloss for a word they know loses a second;
a learner missing a word they don't know loses the whole sentence. When unsure, include the item.

For each line, output the items worth annotating:
1. Candidate words: return EVERY candidate. The only ones you may leave out are names of people /
   companies / places / products, sound effects ("[laughter]"), interjections and filler words
   (yeah, um, wow, bye).
   - Misspelled or garbled words (subtitles are often auto-generated) and invented words: infer the
     intended word from context and still gloss it, e.g. "unmarable" (= unmarriageable) → 嫁不出去的.
     Learners can't look these up, so give them level 10000 or higher.
2. ADD what a frequency list can't see. Read EVERY line, even with no candidates:
   - phrasal verbs / idioms / fixed expressions whose meaning isn't obvious from the single words,
     e.g. "get this right" (做对), "take on" (承担), "thus far" (迄今), "call for" (需要),
     "end to end" (端到端), "bring on" (引入), "turn out to be" (结果是), "at the heart of" (核心)
   - common words used in an uncommon sense here, e.g. "upset the order" (打乱, not 难过),
     "address these risks" (应对, not 地址)
   These are the most valuable annotations — don't skip them.
   But do NOT add transparent phrases whose meaning is just the sum of easy words — a learner who
   knows the words already understands them: go away, call me back, go to the bathroom,
   take it with you, what's going on, give me a call, wait a minute.
3. For each item:
   - "span": copied EXACTLY from the line (same case and spelling). For an expression, the whole
     expression, but no extra words around it ("retrieve", not "retrieve information").
   - "zh": ONE short Simplified Chinese gloss for THIS context, ideally 2-6 characters.
     No "/", "；", alternatives, parentheses or explanations.
   - "kind": "word" | "phrase" | "sense"   (sense = common word used in an uncommon meaning)
   - "level": vocabulary size at which a typical Chinese learner understands this item IN THIS
     MEANING. One of: 1500, 2000, 2500, 3000, 4000, 5000, 6000, 8000, 10000, 12000, 15000, 20000.
     Calibrate to how Chinese learners actually build vocabulary — through exam word lists:
       junior-high list ≈ 1500-2000 · senior-high syllabus (3500 words) ≈ 3000-3500 ·
       CET-4 ≈ 4500 · CET-6 / 考研 ≈ 6000 · IELTS / TOEFL ≈ 8000 · GRE ≈ 12000+.
     A "3000" learner is a typical high-school student: a word outside the senior-high syllabus is
     4000 or higher, however common it is for native speakers.
     Everyday words that textbooks rarely teach are often UNKNOWN to Chinese learners even though
     natives use them constantly: household objects, food, clothing, body parts, chores, family life
     (pillow, toes, throat, knapsack, jello, nap, mop, faucet, sneeze) → 4000-6000.
     Colloquial expressions and phrasal verbs whose meaning can NOT be worked out from the single
     words (freak out, hang out, how come, keep someone on their toes, upside down, my bad) → 4000+.
     Slang → 6000+.
     For "phrase" and "sense", estimate when the MEANING becomes clear — often 4000+ even though
     the single words are easy.
     Reference points (word: level): mathematics 2000, biology 2500, coding 3000, discovery 2500,
     enormous 4000, determined 3000, essence 5000, profound 6000, implication 5000, cognition 8000,
     deploy 6000, sharpen 5000, replicate 6000, multiplicative 12000, "take on" (承担) 5000,
     "port" (移植代码, sense) 8000, "thus far" 6000, pillow 4000, gossip 5000, fundamentally 5000.

Rules: never annotate names; at most 6 items per line; skip items below level 1500;
lines with nothing worth annotating get an empty list.

Reply with JSON only:
{"items": [{"id": <line id>, "notes": [{"span": "...", "zh": "...", "kind": "word", "level": 5000}]}]}"""

GLOSS_PROMPT = """Chinese learners of English need short glosses for these words from video subtitles.
Each item has id, word, and the line it appears in (with prev / next lines for context).
The word may be misspelled (subtitles are often auto-generated) or invented: infer the intended word
from context and gloss that.
For each item return:
- "zh": ONE short Simplified Chinese gloss for this context, 2-6 characters, no alternatives
- "level": vocabulary size at which a typical Chinese learner knows it, one of 1500, 2000, 2500, 3000,
  4000, 5000, 6000, 8000, 10000, 12000, 15000, 20000 (misspelled / invented words: 10000+;
  everyday objects, food, body parts textbooks skip: 4000-6000)
- or "skip": true if the word is a person's name, a sound effect, an interjection or a filler word.
Reply with JSON only: {"items": [{"id": 0, "zh": "...", "level": 6000}]}"""

FALLBACK_RANK = 4000   # rare candidates the annotation step skipped get a second, focused pass


def missing_candidates(texts: list[str], ids, cands: list[list[dict]], placed: dict) -> list[tuple[int, str]]:
    """Rare candidate words (rank >= FALLBACK_RANK) that no annotation on their line covers."""
    out = []
    for i in ids:
        spans = [texts[i][n["s"]:n["e"]].lower() for n in placed.get(i, [])]
        for c in cands[i]:
            if c["rank"] >= FALLBACK_RANK and not any(c["word"].lower() in s for s in spans):
                out.append((i, c["word"]))
    return out


def gloss_missing(client, model: str, effort: str, texts: list[str], missing: list[tuple[int, str]],
                  usage: dict | None = None) -> dict[int, list[dict]]:
    """Second chance for skipped rare words -> extra raw notes per line {line id: [note, ...]}."""
    extra: dict[int, list[dict]] = {}
    for k0 in range(0, len(missing), 40):
        chunk = missing[k0:k0 + 40]
        payload = []
        for k, (i, w) in enumerate(chunk):
            ctx = context_item(texts, i)
            payload.append({"id": k, "word": w, "prev": ctx["prev"], "line": ctx["line"], "next": ctx["next"]})
        data = call_json(client, model, effort, GLOSS_PROMPT, payload, usage=usage)
        for it in data.get("items", []):
            try:
                k = int(it["id"])
            except (KeyError, TypeError, ValueError):
                continue
            if not (0 <= k < len(chunk)) or it.get("skip") or not str(it.get("zh", "")).strip():
                continue
            i, w = chunk[k]
            extra.setdefault(i, []).append({"span": w, "zh": it["zh"], "kind": "word", "level": it.get("level", 8000)})
    return extra


TRANSLATE_PROMPT = """Translate English video subtitle lines into natural Simplified Chinese subtitles.
Each item has id, prev, line, next. A sentence may continue across lines: translate ONLY the words
in THIS line (not the rest of the sentence from prev/next), using the neighbours just to get the
meaning right. Be concise like real subtitles. Keep product and company names in English.
Return exactly one item per input item, same ids, same order. "src" = the first 4 words of the
line, copied exactly, so the translation can be matched to its line.

Reply with JSON only: {"items": [{"id": <line id>, "src": "first four words", "zh": "..."}]}"""


_usage_lock = threading.Lock()
USAGE_FIELDS = ("calls", "input", "cached_input", "output", "reasoning")


def record_usage(resp, model: str, effort: str, label: str, sink: dict | None) -> None:
    """Token usage of one API call -> .cache/usage.jsonl, and summed into sink[label] if given.
    'output' includes 'reasoning' (gpt-5 models bill their hidden reasoning as output tokens)."""
    u = getattr(resp, "usage", None)
    if not u:
        return
    din, dout = getattr(u, "prompt_tokens_details", None), getattr(u, "completion_tokens_details", None)
    cached = getattr(din, "cached_tokens", 0) or getattr(u, "prompt_cache_hit_tokens", 0) or 0   # DeepSeek: the latter
    rec = {"calls": 1, "input": u.prompt_tokens or 0, "cached_input": cached,
           "output": u.completion_tokens or 0, "reasoning": getattr(dout, "reasoning_tokens", 0) or 0}
    with _usage_lock:
        if sink is not None:
            acc = sink.setdefault(label, dict.fromkeys(USAGE_FIELDS, 0))
            for k in USAGE_FIELDS:
                acc[k] += rec[k]
        CACHE_DIR.mkdir(exist_ok=True)
        with open(CACHE_DIR / "usage.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "model": model, "effort": effort,
                                "label": label, **rec}) + "\n")


def usage_total(sink: dict) -> dict:
    return {k: sum(v.get(k, 0) for v in sink.values()) for k in USAGE_FIELDS}


def call_json(client, model: str, effort: str, system: str, payload, usage: dict | None = None) -> dict:
    blob = json.dumps([model, effort, system, payload], ensure_ascii=False)
    cache_file = CACHE_DIR / f"{hashlib.sha256(blob.encode()).hexdigest()[:24]}.json"
    if cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))   # cached: costs nothing
    label = {ANNOTATE_PROMPT: "annotate", TRANSLATE_PROMPT: "translate", GLOSS_PROMPT: "gloss"}.get(system, "other")
    import providers
    extra = providers.chat_options(model, effort)    # GPT reasoning effort / DeepSeek & MiMo thinking switch
    last_err = None
    for _ in range(2):
        try:
            resp = client.chat.completions.create(
                model=model,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                **extra,
            )
            record_usage(resp, model, effort, label, usage)   # billed even if the JSON turns out bad
            text = (resp.choices[0].message.content or "").strip()
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)  # some models wrap JSON in a code fence
            data = json.loads(text)
            CACHE_DIR.mkdir(exist_ok=True)
            cache_file.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            return data
        except Exception as e:  # malformed JSON or transient API error: retry once
            last_err = e
    print(f"  ! batch failed: {last_err}", flush=True)
    return {"items": []}


RUN_USAGE: dict = {}   # token usage of this command-line run


def run_batches(label, ids, make_item, system, model, effort, batch_size, workers=8):
    from openai import OpenAI

    client = OpenAI()
    batches = [ids[k:k + batch_size] for k in range(0, len(ids), batch_size)]

    def run(batch):
        return call_json(client, model, effort, system, [make_item(i) for i in batch], usage=RUN_USAGE)

    results = []
    with ThreadPoolExecutor(workers) as pool:
        for n, data in enumerate(pool.map(run, batches), 1):
            results.extend(data.get("items", []))
            print(f"  {label}: batch {n}/{len(batches)}", flush=True)
    return results


def context_item(texts, i):
    return {
        "id": i,
        "prev": texts[i - 1] if i > 0 else "",
        "line": texts[i],
        "next": texts[i + 1] if i + 1 < len(texts) else "",
    }


# ------------------------------------------------------------ post-process ---

def clean_zh(zh: str) -> str:
    """Keep one gloss: drop parenthesised asides, keep the first of '/'- or '；'-separated options."""
    zh = re.sub(r"[（(][^）)]*[）)]", "", str(zh))
    zh = re.split(r"[/／;；,，、|]", zh)[0]
    return zh.strip()


def place_notes(text: str, notes: list[dict]) -> list[dict]:
    """Locate each span in the text -> [{s, e, zh, lv, kind}] sorted, non-overlapping.
    On overlap the longer span (usually the expression) wins."""
    found = []
    for n in notes:
        span, zh = str(n.get("span", "")).strip(), clean_zh(n.get("zh", ""))
        if not span or not zh:
            continue
        pat = r"(?<!\w)" + re.escape(span) + r"(?!\w)"
        m = re.search(pat, text) or re.search(pat, text, re.IGNORECASE)
        if not m:
            continue
        kind = n.get("kind") if n.get("kind") in ("word", "phrase", "sense") else "word"
        found.append({"s": m.start(), "e": m.end(), "zh": zh, "lv": snap_level(n.get("level")), "kind": kind})
    found.sort(key=lambda p: p["e"] - p["s"], reverse=True)
    placed = []
    for p in found:
        if not any(p["s"] < q["e"] and q["s"] < p["e"] for q in placed):
            placed.append(p)
    return sorted(placed, key=lambda p: p["s"])


def add_keys(cues: list[dict]) -> None:
    """key = lemmatised lowercase span, so 'multipliers' and 'multiplier' count as one word."""
    spans = [(c, n) for c in cues for n in c["notes"]]
    docs = nlp().pipe(c["text"][n["s"]:n["e"]] for c, n in spans)
    for (c, n), d in zip(spans, docs):
        n["key"] = " ".join(t.lemma_.lower() for t in d if not (t.is_punct or t.is_space)) or c["text"][n["s"]:n["e"]].lower()


def blend_word_levels(cues: list[dict]) -> None:
    """Single words: when the frequency rank says a word is rarer than the LLM thought, raise the
    level toward it (log-space blend, 65/35). Never lower the LLM's level: missing an unknown word
    costs the learner more than an extra gloss, so errors should lean toward glossing."""
    import math
    ranks = load_ranks()
    for c in cues:
        for n in c["notes"]:
            if n["kind"] == "word" and " " not in n["key"]:
                r = min(ranks.get(n["key"], 30000), 30000)
                blended = math.exp(0.65 * math.log(n["lv"]) + 0.35 * math.log(r))
                n["lv"] = snap_level(max(n["lv"], blended))


def translate_lines(texts, model, effort) -> dict[int, str]:
    """Per-line translation with an alignment check (model echoes the line's first words);
    lines that fail the check are retried in small batches."""
    def words(s, n=3):
        return " ".join(re.findall(r"[a-z0-9']+", s.lower().replace("’", "'"))[:n])

    out: dict[int, str] = {}
    todo = list(range(len(texts)))
    for batch_size in (15, 4):
        items = run_batches("translate", todo, lambda i: context_item(texts, i), TRANSLATE_PROMPT,
                            model, effort, batch_size)
        for it in items:
            try:
                i = int(it["id"])
            except (KeyError, ValueError, TypeError):
                continue
            if 0 <= i < len(texts) and str(it.get("zh", "")).strip() and words(str(it.get("src", ""))) == words(texts[i]):
                out[i] = str(it["zh"]).strip()
        todo = [i for i in range(len(texts)) if i not in out]
        if not todo:
            break
    return out


def unify_glosses(cues: list[dict]) -> None:
    """Same word + same kind -> same gloss everywhere (first one wins)."""
    first: dict[tuple, dict] = {}
    for c in cues:
        for n in c["notes"]:
            k = (n["key"], n["kind"])
            if k in first:
                n["zh"], n["lv"] = first[k]["zh"], first[k]["lv"]
            else:
                first[k] = n


def visible_notes(cues: list[dict], level: int, max_repeat: int = 2, per_line: int = 3) -> list[list[dict]]:
    """Same rule as the web player: level filter, repeat cap, per-line cap (hardest first)."""
    count: dict[str, int] = {}
    out = []
    for c in cues:
        cand = sorted((n for n in c["notes"] if n["lv"] > level), key=lambda n: -n["lv"])
        keep = []
        for n in cand:
            if len(keep) >= per_line or count.get(n["key"], 0) >= max_repeat:
                continue
            count[n["key"]] = count.get(n["key"], 0) + 1
            keep.append(n)
        out.append(sorted(keep, key=lambda n: n["s"]))
    return out


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def update_index(data_dir: Path, video_id: str, title: str, file: str) -> None:
    idx_path = data_dir / "index.json"
    try:
        idx = json.loads(idx_path.read_text(encoding="utf-8"))
        if not isinstance(idx, list) or (idx and not isinstance(idx[0], dict)):
            idx = []
    except (OSError, json.JSONDecodeError):
        idx = []
    idx = [v for v in idx if v.get("id") != video_id] + [{"id": video_id, "title": title, "file": file}]
    idx_path.write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")


# -------------------------------------------------------------------- main ---

def main():
    load_dotenv(PROJECT / ".env")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("srt_file")
    ap.add_argument("--title", default="")
    ap.add_argument("--model", default=os.environ.get("OPENAI_MODEL", "gpt-6-luna"))
    ap.add_argument("--effort", default="medium",
                    help="reasoning effort for the annotation step (gpt-5 / o-series); translation uses low")
    ap.add_argument("--dry-run", action="store_true", help="frequency step only, no API calls")
    ap.add_argument("--export-srt", type=int, metavar="LEVEL", help="write an .srt for one level from the JSON")
    args = ap.parse_args()

    src = Path(args.srt_file)
    video_id = src.name.split(".")[0]
    out_json = src.with_name(f"{video_id}.json")

    if args.export_srt:
        data = json.loads(out_json.read_text(encoding="utf-8"))
        shown = visible_notes(data["cues"], args.export_srt)
        subs = []
        for i, (c, notes) in enumerate(zip(data["cues"], shown), 1):
            t = c["text"]
            for n in sorted(notes, key=lambda n: n["e"], reverse=True):
                t = t[:n["e"]] + f"({n['zh']})" + t[n["e"]:]
            subs.append(srt.Subtitle(i, srt.timedelta(seconds=c["s"]), srt.timedelta(seconds=c["e"]), t))
        out = src.with_name(f"{video_id}.v{args.export_srt}.srt")
        out.write_text(srt.compose(subs), encoding="utf-8-sig")
        print(f"wrote {out} ({sum(map(len, shown))} annotations)")
        return

    raw = list(srt.parse(src.read_text(encoding="utf-8-sig")))
    texts = [clean(c.content) for c in raw]
    print(f"{len(raw)} cues")
    cands = find_candidates(texts)
    print(f"frequency step: {sum(map(len, cands))} candidates (rank > {MIN_RANK})")

    if args.dry_run:
        notes_by_id = {i: [{"span": c["word"], "zh": "?", "kind": "word", "level": c["rank"]} for c in cs]
                       for i, cs in enumerate(cands)}
        zh_by_id = {}
    else:
        if not os.environ.get("OPENAI_API_KEY"):
            sys.exit("OPENAI_API_KEY is not set. Put it in a .env file next to annotate.py, or use --dry-run.")
        print(f"model {args.model} (effort={args.effort})")

        def ann_item(i):
            it = context_item(texts, i)
            it["candidates"] = [{"word": c["word"], "rank": c["rank"]} for c in cands[i]]
            return it

        ann = run_batches("annotate", list(range(len(texts))), ann_item, ANNOTATE_PROMPT,
                          args.model, args.effort, 25)
        notes_by_id = {int(it["id"]): it.get("notes", []) for it in ann if "id" in it}
        # second chance for rare words the annotation step skipped
        placed = {i: place_notes(texts[i], notes_by_id.get(i, [])) for i in range(len(texts))}
        missing = missing_candidates(texts, range(len(texts)), cands, placed)
        if missing:
            from openai import OpenAI
            extra = gloss_missing(OpenAI(), args.model, "low", texts, missing, usage=RUN_USAGE)
            for i, ns in extra.items():
                notes_by_id[i] = notes_by_id.get(i, []) + ns
            print(f"fallback: {len(missing)} skipped rare words, {sum(map(len, extra.values()))} glossed")
        zh_by_id = translate_lines(texts, args.model, "low")

    cues = []
    for i, c in enumerate(raw):
        cues.append({
            "s": round(c.start.total_seconds(), 3),
            "e": round(c.end.total_seconds(), 3),
            "text": texts[i],
            "zh": zh_by_id.get(i, ""),
            "notes": place_notes(texts[i], notes_by_id.get(i, [])),
        })
    add_keys(cues)
    if not args.dry_run:
        blend_word_levels(cues)
    unify_glosses(cues)

    data = {
        "video": {"id": video_id, "title": args.title or video_id,
                  "duration": cues[-1]["e"] if cues else 0},
        "generated": date.today().isoformat(),
        "model": None if args.dry_run else args.model,
        "cues": cues,
    }
    if args.dry_run:
        out_json = src.with_name(f"{video_id}.dry-run.json")
        write_json(out_json, {**data, "status": "preview"})
    else:
        SS.publish(out_json, data)
        update_index(src.parent, video_id, data["video"]["title"], out_json.name)

    total = sum(len(c["notes"]) for c in cues)
    kinds = {k: sum(n["kind"] == k for c in cues for n in c["notes"]) for k in ("word", "phrase", "sense")}
    missing_zh = sum(not c["zh"] for c in cues)
    print(f"annotations: {total}  {kinds}   lines without translation: {missing_zh}")
    for lv in (2000, 3000, 5000, 8000, 12000):
        shown = sum(map(len, visible_notes(cues, lv)))
        print(f"  level {lv:>5}: {shown:>4} shown ({shown / len(cues):.2f} per line)")
    if RUN_USAGE:
        t = usage_total(RUN_USAGE)
        print(f"tokens: input {t['input']:,} (cached {t['cached_input']:,}), output {t['output']:,} "
              f"(of which reasoning {t['reasoning']:,}), {t['calls']} API calls")
    print(f"wrote {out_json}")


if __name__ == "__main__":
    main()
