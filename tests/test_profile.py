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


@pytest.mark.parametrize("body", [
    {"reset_marks": "false"}, {"reset_marks": "true"}, {"reset_marks": 0}, {"reset_marks": 1},
    {"reset_marks": None}, {"reset_marks": []}, {"reset_marks": {}},
    {"level": False}, {"level": True}, {"level": -100}, {"level": 0}, {"level": 100001}, {"level": 5000.5},
    {"exams": {"ielts": True}}, {"exams": {"cet4": False}}, {"exams": {"cet_4": 600}},
    {"field": {"unexpected": "value"}}, {"test": {"est": True}}, {"test": {"est": 4000.9}},
    {"test": {"est": 4000, "bands": [True]}}, {"test": {"est": 4000, "known": ["bank", "bad123"]}},
    {"test": {"est": 4000, "unknown": [""]}}, {"known": []},
])
def test_invalid_profile_input_preserves_all_fields_revision_and_marks(tmp_path, body):
    store = Store(tmp_path / "profile.json")
    store.update({"identity": "undergrad", "exams": {"cet4": "560"}})
    store.mark("bank", "known")
    before = store.path.read_bytes()
    assert store.update(body)
    assert store.path.read_bytes() == before


def test_explicit_false_reset_and_valid_form_numbers_preserve_learning_marks(tmp_path):
    store = Store(tmp_path / "profile.json")
    store.mark("bank", "known")
    assert store.update({"reset_marks": False, "level": "4600", "exams": {"ielts": "6.5"}, "field": None}) is None
    saved = store.load()
    assert saved["known"] == ["bank"] and saved["level"] == 5000 and saved["exams"] == {"ielts": 6.5}
    assert store.update({"test": {"est": "4500", "bands": ["0.5", 1, 0], "known": ["Leaf", "leaf"]}}) is None
    assert store.load()["test"]["known"] == ["leaf"]
    assert store.update({"reset_marks": True}) is None
    assert store.load()["known"] == []


@pytest.mark.parametrize("key", [True, False, 123, None, ["bank"], {"key": "bank"}])
def test_non_text_mark_keys_are_not_silently_converted_to_words(tmp_path, key):
    store = Store(tmp_path / "profile.json")
    store.mark("bank", "known")
    before = store.path.read_bytes()
    assert store.mark(key, "known")
    assert store.path.read_bytes() == before
