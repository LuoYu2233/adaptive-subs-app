"""Build the vocabulary-test word bank: app/data/vocab_test.json

    .venv/Scripts/python tools/make_vocab_test.py     # uses the OpenAI key in .env

- 9 frequency bands (wordfreq ranks). For each band we sample candidate words, and the LLM
  keeps clean test items (no names / brands / slang) with a Chinese meaning, an example
  sentence and 3 wrong options (used for the meaning check that catches over-claiming).
- Pseudo-words (pronounceable, not real) catch "yes to everything" answering.
The profile page samples words adaptively (more near the learner's level), so a retest shows different words.
"""

import os
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from annotate import PROJECT, ROOT, call_json, load_dotenv, nlp  # noqa: E402
from storage import write_json  # noqa: E402

BANDS = [(1, 1000), (1001, 2000), (2001, 3000), (3001, 4500),
         (4501, 6000), (6001, 8000), (8001, 11000), (11001, 15000), (15001, 20000)]

BANK_PROMPT = """You build a yes/no vocabulary test for Chinese learners of English.
Input: {"band": "...", "words": [...]} — words from one frequency band.
Pick up to 30 words that make good test items: common standalone English words in base form
(singular noun, base verb, adjective, adverb). NOT names, places, brands, abbreviations,
interjections, slang, offensive words, foreign words, or words mostly seen inside names.
For each give:
- "zh": its most common Chinese meaning, 2-6 characters
- "example": a short natural English sentence (6-12 words) using the word in that meaning
- "distractors": 3 wrong Chinese meanings, same part of speech, plausible but clearly different
  All four answer texts must be distinct. Never use "不确定": the UI adds that option separately.
Reply with JSON only:
{"items": [{"word": "...", "zh": "...", "example": "...", "distractors": ["...", "...", "..."]}]}"""

NONWORD_PROMPT = """Invent 40 pronounceable fake English words that look like real English words
(5-10 letters, normal English spelling patterns, prefixes and suffixes) but are NOT real words in
any dictionary. No real words, names, brands, or words from other languages.
Reply with JSON only: {"items": ["...", "..."]}"""


def main():
    from openai import OpenAI
    from wordfreq import top_n_list, zipf_frequency
    load_dotenv(PROJECT / ".env")
    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("OPENAI_API_KEY is not set (.env).")
    model = os.environ.get("OPENAI_MODEL", "gpt-6-luna")
    client = OpenAI()
    random.seed(7)

    top = top_n_list("en", 21000)
    bands_out, seen_words = [], set()
    for lo, hi in BANDS:
        pool = []
        for w in top[lo - 1:hi]:
            if not (re.fullmatch(r"[a-z][a-z'-]{0,39}", w) and len(w) >= 3):
                continue
            t = nlp()(w)[0]
            if t.is_stop or t.lemma_.lower() != w or t.pos_ in ("PROPN", "INTJ", "X"):
                continue
            pool.append(w)
        sample = random.sample(pool, min(80, len(pool)))
        data = call_json(client, model, "low", BANK_PROMPT, {"band": f"ranks {lo}-{hi}", "words": sample})
        items = []
        for it in data.get("items", []):
            if not isinstance(it, dict) or not isinstance(it.get("distractors"), list):
                continue
            zh, example = it.get("zh"), it.get("example")
            if not isinstance(zh, str) or not 0 < len(zh.strip()) <= 80 or zh.strip() == "不确定" or not isinstance(example, str) or not 0 < len(example.strip()) <= 400:
                continue
            ds = list(dict.fromkeys(d.strip() for d in it["distractors"] if isinstance(d, str)
                and 0 < len(d.strip()) <= 80 and d.strip() not in (zh.strip(), "不确定")))
            if it.get("word") in sample and it["word"] not in seen_words and len(ds) >= 3:
                items.append({"word": it["word"], "zh": zh.strip(), "example": example.strip(), "distractors": ds[:3]})
                seen_words.add(it["word"])
        if len(items) < 11:
            raise ValueError(f"band {lo}-{hi} has too few valid questions; the existing bank has not been modified")
        bands_out.append({"lo": lo, "hi": hi, "words": items})
        print(f"band {lo:>5}-{hi:<5}: {len(items)} items  e.g. {[i['word'] for i in items[:6]]}")

    data = call_json(client, model, "low", NONWORD_PROMPT, {"n": 40})
    real = set(top_n_list("en", 100_000)) | seen_words
    nonwords = sorted({w.lower() for w in data.get("items", [])
                       if isinstance(w, str) and re.fullmatch(r"[a-z][a-z'-]{0,39}", w.lower())
                       and w.lower() not in real and zipf_frequency(w, "en") == 0})
    if not 6 <= len(nonwords) <= 200:
        raise ValueError("Invalid pseudo-word count; the existing bank has not been modified")
    print(f"pseudo-words: {len(nonwords)}  e.g. {nonwords[:8]}")

    out = ROOT / "data" / "vocab_test.json"
    write_json(out, {"bands": bands_out, "nonwords": nonwords})
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
