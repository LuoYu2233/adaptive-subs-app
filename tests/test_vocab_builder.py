"""Bank generation is exercised with fake model replies and no provider/network access."""
import importlib.util
import json
import string
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def builder(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("test_vocab_builder_tool", Path(__file__).parents[1] / "tools/make_vocab_test.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    words = ["sample" + letter for letter in string.ascii_lowercase[:24]]
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=lambda: object()))
    monkeypatch.setitem(sys.modules, "wordfreq", SimpleNamespace(top_n_list=lambda *_: words, zipf_frequency=lambda *_: 0))
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-builder-test-key")
    monkeypatch.setattr(module, "load_dotenv", lambda *_: None)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "BANDS", [(1,12),(13,24)])
    monkeypatch.setattr(module, "nlp", lambda: lambda w: [SimpleNamespace(is_stop=False, lemma_=w, pos_="NOUN")])
    path = tmp_path / "data/vocab_test.json"
    path.parent.mkdir()
    path.write_bytes(b"original question bank")
    return module, path


def answers(module, issue=None):
    def reply(_client, _model, _effort, prompt, payload):
        if prompt == module.NONWORD_PROMPT:
            return {"items": ["norf" + c for c in string.ascii_lowercase[:5 if issue == "short-fake-list" else 8]]}
        words = payload["words"]
        items = [{"word": w, "zh": "正确意思", "example": f"This original sentence contains {w}.",
                  "distractors": ["错误甲", "错误乙", "错误丙"]} for w in words]
        if issue == "unsure":
            for item in items: item["distractors"][0] = "不确定"
        if issue == "duplicates":
            for item in items: item["distractors"][1] = item["distractors"][0]
        if issue == "repeated-word": items = [items[0]] * 12
        if issue == "incomplete": items = items[:10]
        if issue == "malformed": items = [None, {}, {"distractors": "bad"}]
        return {"items": items}
    return reply


@pytest.mark.parametrize("issue", ["unsure", "duplicates", "repeated-word", "incomplete", "malformed", "short-fake-list"])
def test_invalid_generated_bank_preserves_the_previous_file(builder, monkeypatch, issue):
    module, path = builder
    monkeypatch.setattr(module, "call_json", answers(module, issue))
    with pytest.raises(ValueError, match="existing bank has not been modified"):
        module.main()
    assert path.read_bytes() == b"original question bank"


def test_valid_generated_bank_is_written_once_with_unique_options(builder, monkeypatch):
    module, path = builder
    monkeypatch.setattr(module, "call_json", answers(module))
    module.main()
    bank = json.loads(path.read_text())
    assert [len(b["words"]) for b in bank["bands"]] == [12,12]
    assert len(bank["nonwords"]) == 8
    assert len({w["word"] for b in bank["bands"] for w in b["words"]}) == 24
    assert all(len(set([w["zh"],*w["distractors"]])) == 4 for b in bank["bands"] for w in b["words"])
