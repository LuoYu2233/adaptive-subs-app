"""Build experimental lemma difficulties from the local wordfreq and English-model data."""
import argparse
from collections import Counter, defaultdict
from hashlib import sha256
from importlib.metadata import version
from itertools import groupby
import json
import math
from pathlib import Path
import re
from statistics import median
import tempfile

ROOT = Path(__file__).resolve().parents[1]
EXAMS = ROOT / "app/data/exam_words.json"
STAGES = (("zk",), ("gk",), ("cet4",), ("cet6", "ky"), ("ielts", "toefl"), ("gre",))
WORD = re.compile(r"[a-z]+(?:-[a-z]+)*")


def exam_stages(data):
    tags = data["tags"]
    if not isinstance(tags, list) or len(set(tags)) != len(tags) or not all(tag in tags for group in STAGES for tag in group):
        raise ValueError("Invalid exam tag index")
    masks = [sum(1 << tags.index(tag) for tag in group) for group in STAGES]
    stages = {"a": None, "i": None}  # The project's exam dictionary omits one-letter headwords.
    for word, (mask, _) in data["words"].items():
        if type(mask) is not int or not 0 <= mask < 1 << len(tags):
            raise ValueError(f"Invalid exam mask for {word}")
        if WORD.fullmatch(word):
            stages[word] = next((i for i, bits in enumerate(masks) if mask & bits), None)
    return stages


def morphology(lookups, headwords):
    """Use the English model's exceptions and POS-specific suffix rules, limited to dictionary roots."""
    tables = []
    for pos in ("noun", "verb", "adj", "adv"):
        index = headwords & set(lookups.get_table("lemma_index").get(pos, []))
        rules = lookups.get_table("lemma_rules").get(pos, [])
        exceptions = lookups.get_table("lemma_exc").get(pos, {})
        tables.append((index, rules, exceptions))

    def candidates(form):
        roots = set()
        for index, rules, exceptions in tables:
            if form in exceptions:
                roots.update(root for root in exceptions[form] if root in headwords)
            else:
                for suffix, replacement in rules:
                    if suffix and form.endswith(suffix):
                        root = form[:-len(suffix)] + replacement
                        if root in index:
                            roots.add(root)
        return roots
    return candidates


def build_table(frequencies, stages, candidates, limit, stage_weight):
    if type(limit) is not int or limit < 1 or not 0 <= stage_weight <= 1:
        raise ValueError("limit must be positive and stage weight must be between 0 and 1")
    totals = defaultdict(list)
    coverage = Counter()
    # Stable input order also makes equal-mass ranks reproducible.
    for form, frequency in sorted(frequencies.items()):
        if not WORD.fullmatch(form):
            coverage["nonword"] += 1
            continue
        if not math.isfinite(frequency) or frequency <= 0:
            raise ValueError(f"Invalid frequency for {form}")
        if form in stages:
            lemma = form
            coverage["headword"] += 1
        else:
            roots = candidates(form)
            if len(roots) != 1:
                coverage["ambiguous" if roots else "unmatched"] += 1
                continue
            lemma, = roots
            coverage["inflection"] += 1
        totals[lemma].append(frequency)
    if not totals:
        raise ValueError("No dictionary lemmas matched the frequency data")

    mass = {word: math.fsum(values) for word, values in totals.items()}
    ordered = sorted(mass, key=lambda word: (-mass[word], word))
    ranks, first = {}, 1
    for _, words in groupby(ordered, key=mass.__getitem__):
        tied = list(words)
        rank = first + (len(tied) - 1) / 2
        ranks.update(dict.fromkeys(tied, rank))
        first += len(tied)
    # Estimate each stage's typical log rank from the entire matched lexicon before truncation.
    groups = defaultdict(list)
    for word, rank in ranks.items():
        if stages[word] is not None:
            groups[stages[word]].append(math.log(rank))
    centers = {stage: median(values) for stage, values in groups.items()}
    entries = {}
    for word in ordered[:limit]:
        rank, stage = ranks[word], stages[word]
        frequency_b = math.log(rank)
        initial_b = frequency_b if stage is None else frequency_b + stage_weight * (centers[stage] - frequency_b)
        entries[word] = {"rank": rank, "forms": len(totals[word]), "stage": stage, "letters": len(word.replace("-", "")),
                         "b_frequency": round(frequency_b, 8), "b_initial": round(initial_b, 8)}
    return {"version": 1, "scale": "natural-log lemma rank", "stage_weight": stage_weight,
            "stages": STAGES, "stage_centers": {k: round(v, 8) for k, v in centers.items()},
            "coverage": dict(coverage), "matched_lemmas": len(ranks), "words": entries}


def generate(limit, weight):
    import en_core_web_sm
    from wordfreq import get_frequency_dict

    raw = EXAMS.read_bytes()
    stages = exam_stages(json.loads(raw))
    model = en_core_web_sm.load(enable=["lemmatizer"])
    candidates = morphology(model.get_pipe("lemmatizer").lookups, stages.keys())
    table = build_table(get_frequency_dict("en", wordlist="large"), stages, candidates, limit, weight)
    table["sources"] = {"wordfreq": version("wordfreq"), "wordlist": "en-large", "spacy": version("spacy"),
                        "en_core_web_sm": version("en-core-web-sm"), "exam_sha256": sha256(raw).hexdigest()}
    return table


def write_table(path, table):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=path.parent,
                                         prefix=f".{path.name}.", suffix=".tmp", delete=False) as out:
            temporary = Path(out.name)
            json.dump(table, out, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            out.write("\n")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "build/word-difficulty-v1.json")
    parser.add_argument("--limit", type=int, default=30000)
    parser.add_argument("--stage-weight", type=float, default=0.20)
    args = parser.parse_args()
    if args.output.resolve() == EXAMS.resolve():
        parser.error("output must be different from the source dictionary")
    table = generate(args.limit, args.stage_weight)
    write_table(args.output, table)
    print(f"{len(table['words'])} entries from {table['matched_lemmas']} matched lemmas -> {args.output}")


if __name__ == "__main__":
    main()
