"""Build the vocabulary-test word bank: data/vocab_test.json

    python make_vocab_test.py

- 8 frequency bands (wordfreq ranks). For each band we sample candidate words, and the LLM
  keeps clean test items (no names / brands / slang) with a Chinese meaning, an example
  sentence and 3 wrong options (used for the meaning check that catches over-claiming).
- Pseudo-words (pronounceable, not real) catch "yes to everything" answering.
The player samples 6 words per band each time, so a retest shows different words.
"""

import json
import os
import random
import sys
from openai import OpenAI
from wordfreq import top_n_list, zipf_frequency

from annotate import ROOT, call_json, load_dotenv, nlp

BANDS = [(1, 1000), (1001, 2000), (2001, 3000), (3001, 4500),
         (4501, 6000), (6001, 8000), (8001, 11000), (11001, 15000)]

BANK_PROMPT = """You build a yes/no vocabulary test for Chinese learners of English.
Input: {"band": "...", "words": [...]} — words from one frequency band.
Pick up to 14 words that make good test items: common standalone English words in base form
(singular noun, base verb, adjective, adverb). NOT names, places, brands, abbreviations,
interjections, slang, offensive words, foreign words, or words mostly seen inside names.
For each give:
- "zh": its most common Chinese meaning, 2-6 characters
- "example": a short natural English sentence (6-12 words) using the word in that meaning
- "distractors": 3 wrong Chinese meanings, same part of speech, plausible but clearly different
Reply with JSON only:
{"items": [{"word": "...", "zh": "...", "example": "...", "distractors": ["...", "...", "..."]}]}"""

NONWORD_PROMPT = """Invent 40 pronounceable fake English words that look like real English words
(5-10 letters, normal English spelling patterns, prefixes and suffixes) but are NOT real words in
any dictionary. No real words, names, brands, or words from other languages.
Reply with JSON only: {"items": ["...", "..."]}"""


def main():
    load_dotenv(ROOT / ".env")
    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("OPENAI_API_KEY is not set (.env).")
    model = os.environ.get("OPENAI_MODEL", "gpt-6-luna")
    client = OpenAI()
    random.seed(7)

    top = top_n_list("en", 16000)
    bands_out = []
    for lo, hi in BANDS:
        pool = []
        for w in top[lo - 1:hi]:
            if not (w.isalpha() and w.islower() and len(w) >= 3):
                continue
            t = nlp()(w)[0]
            if t.is_stop or t.lemma_.lower() != w or t.pos_ in ("PROPN", "INTJ", "X"):
                continue
            pool.append(w)
        sample = random.sample(pool, min(45, len(pool)))
        data = call_json(client, model, "low", BANK_PROMPT, {"band": f"ranks {lo}-{hi}", "words": sample})
        items = []
        for it in data.get("items", []):
            ds = [d for d in it.get("distractors", []) if d and d != it.get("zh")]
            if it.get("word") in sample and it.get("zh") and it.get("example") and len(ds) >= 3:
                items.append({"word": it["word"], "zh": it["zh"], "example": it["example"], "distractors": ds[:3]})
        bands_out.append({"lo": lo, "hi": hi, "words": items})
        print(f"band {lo:>5}-{hi:<5}: {len(items)} items  e.g. {[i['word'] for i in items[:6]]}")

    data = call_json(client, model, "low", NONWORD_PROMPT, {"n": 40})
    real = set(top_n_list("en", 100_000))
    nonwords = sorted({w.lower() for w in data.get("items", [])
                       if w.isalpha() and w.lower() not in real and zipf_frequency(w, "en") == 0})
    print(f"pseudo-words: {len(nonwords)}  e.g. {nonwords[:8]}")

    out = ROOT / "data" / "vocab_test.json"
    out.write_text(json.dumps({"bands": bands_out, "nonwords": nonwords}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
