import pytest

from profile_store import Store


@pytest.mark.parametrize("body", [
    {"identity": []}, {"exams": []}, {"exams": "bad"}, {"level": float("inf")},
    {"test": []}, {"test": {"est": -1}}, {"test": {"est": "Infinity"}},
    {"test": {"est": 3000, "bands": ["bad"]}}, {"test": {"est": 3000, "bands": [2]}},
    {"test": {"est": 3000, "bands": [float("nan")]}},
    {"test": {"est": 3000, "known": "bank"}}, {"test": {"est": 3000, "unknown": [42]}},
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
