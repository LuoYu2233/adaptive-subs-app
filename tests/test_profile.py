import pytest

from profile_store import Store


@pytest.mark.parametrize("body", [
    {"identity": []}, {"exams": []}, {"exams": "bad"}, {"level": float("inf")},
    {"test": []}, {"test": {"est": -1}}, {"test": {"est": "Infinity"}},
    {"test": {"est": 3000, "bands": ["bad"]}}, {"test": {"est": 3000, "bands": [2]}},
    {"test": {"est": 3000, "bands": [float("nan")]}},
    {"test": {"est": 3000, "known": "bank"}}, {"test": {"est": 3000, "unknown": [42]}},
    {"test": {"est": 3000, "known": ["Bank"], "unknown": ["bank"]}},
])
def test_invalid_updates_do_not_damage_existing_profile(tmp_path, body):
    store = Store(tmp_path / "profile.json")
    assert store.update({"identity": "undergrad"}) is None
    assert store.mark("bank", "known") is None
    before = store.path.read_bytes()
    assert store.update({**body, "reset_marks": True})
    assert store.path.read_bytes() == before


def test_valid_profile_test_and_explicit_reset_are_saved_atomically(tmp_path):
    store = Store(tmp_path / "profile.json")
    store.mark("bank", "known")
    assert store.update({"identity": "undergrad", "exams": {"cet4": 560}, "reset_marks": True,
                         "test": {"est": 4600, "bands": [1, 0.5, 0], "known": ["river"], "unknown": ["dock"]}}) is None
    p = store.load()
    assert p["level"] == 5000
    assert p["known"] == [] and p["test"]["unknown"] == ["dock"]


def test_context_marks_keep_separate_meanings_and_old_word_marks(tmp_path):
    store = Store(tmp_path / "profile.json")
    bank, shore = "ctx:bank:0123456789abcdef", "ctx:bank:fedcba9876543210"
    assert store.mark("bank", "known") is None
    assert store.mark(bank, "known", {"term": "bank", "zh": "银行"}) is None
    assert store.mark(shore, "unknown", {"term": "bank", "zh": "河岸"}) is None
    p = Store(store.path).load()
    assert set(p["known"]) == {"bank", bank} and p["unknown"] == [shore]
    assert p["mark_details"][shore]["zh"] == "河岸"
    assert store.mark(bank, "clear") is None
    assert bank not in store.load()["mark_details"]
    assert store.load()["mark_details"][shore]["zh"] == "河岸"


def test_invalid_mark_metadata_leaves_existing_marks_unchanged(tmp_path):
    store = Store(tmp_path / "profile.json")
    store.mark("bank", "known")
    before = store.path.read_bytes()
    assert store.mark("ctx:bank:0123456789abcdef", "known", {"zh": []})
    assert store.path.read_bytes() == before
