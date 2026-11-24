"""Key construction must use the original sentence, not an isolated word's guessed POS."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from unittest.mock import Mock

import annotate as A


def test_key_spans_use_one_original_cue_with_unicode_and_distinct_positions(monkeypatch):
    line = "🌿 She saw a saw."
    tokens = [SimpleNamespace(text=text, idx=index, lemma_=lemma, is_punct=punct, is_space=False)
              for text, index, lemma, punct in [("She", 2, "she", False), ("saw", 6, "see", False),
                  ("a", 10, "a", False), ("saw", 12, "saw", False), (".", 15, ".", True)]]
    inputs = []
    def pipe(texts):
        for text in texts:
            inputs.append(text)
            assert text == line
            yield tokens
    monkeypatch.setattr(A, "nlp", lambda: SimpleNamespace(pipe=pipe))
    cue = {"text": line, "notes": [{"s": 6, "e": 9}, {"s": 12, "e": 15}, {"s": 12, "e": 16}]}
    A.add_keys([{"text": "Unannotated words are not parsed again.", "notes": []}, cue])
    assert inputs == [line]
    assert [n["key"] for n in cue["notes"]] == ["see", "saw", "saw"]
    assert [(n["s"], n["e"]) for n in cue["notes"]] == [(6, 9), (12, 15), (12, 16)]


def test_empty_annotations_do_not_load_a_language_model(monkeypatch):
    def forbidden():
        raise AssertionError("No model is needed for empty annotations")
    monkeypatch.setattr(A, "nlp", forbidden)
    A.add_keys([])
    A.add_keys([{"text": "No notes.", "notes": []}])


def test_installed_english_model_preserves_contextual_lemmas_and_glosses():
    pytest.importorskip("spacy")
    pytest.importorskip("en_core_web_sm")
    cases = json.loads((Path(__file__).parent / "fixtures/context_lemmas.json").read_text())
    cues, expected = [], []
    for case in cases:
        notes = []
        for item in case["notes"]:
            start = -1
            for _ in range(item.get("occurrence", 0) + 1):
                start = case["text"].index(item["span"], start + 1)
            notes.append({"s": start, "e": start + len(item["span"]), "zh": item["zh"], "kind": item["kind"], "lv": 5000})
            expected.append((item["key"], item["zh"], item["kind"]))
        cues.append({"text": case["text"], "notes": notes})
    A.add_keys(cues)
    actual = [(n["key"], n["zh"], n["kind"]) for c in cues for n in c["notes"]]
    assert actual == expected


def test_generation_persists_contextual_keys_and_reuses_the_existing_model_response(tmp_path, monkeypatch):
    pytest.importorskip("spacy")
    pytest.importorskip("en_core_web_sm")
    import server as S
    line = "The leaves fell from the tree."
    data = {"items": [{"id": 0, "notes": [{"span": "leaves", "zh": "树叶", "kind": "word", "level": 5000}]}]}
    create = Mock(return_value=SimpleNamespace(usage=None, choices=[SimpleNamespace(
        message=SimpleNamespace(content=json.dumps(data)))]))
    job = S.Job("abcdefghijk", save_dir=tmp_path / "results")
    job._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    job.title, job.duration, job.texts, job.cands = "Original contextual lemma test", 4, [line], [[]]
    job.cues = [{"s": 0, "e": 4, "text": line}]
    job.zh = {0: "树叶从树上落下。"}
    monkeypatch.setattr(A, "load_ranks", lambda: {"leaf": 5000})
    S.run_annotation(job, [0], "low")
    assert job.ann[0][0]["key"] == "leaf"
    job.ann.clear()
    S.run_annotation(job, [0], "low")
    assert create.call_count == 1
    S.finalize(job)
    saved = json.loads((job.save_dir / f"{job.vid}.json").read_text())
    assert saved["cues"][0]["notes"][0]["key"] == "leaf"
    assert saved["cues"][0]["notes"][0]["zh"] == "树叶"
    assert saved["status"] == "complete"
    assert job.events[-1][0] == "done"
