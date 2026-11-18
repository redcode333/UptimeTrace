from app.ingest.fingerprint import canonical_json, content_hash
from tests.factories import make_envelope


def test_object_key_order_never_changes_the_hash():
    a = {"x": 1, "y": {"p": [1, 2], "q": "s"}}
    b = {"y": {"q": "s", "p": [1, 2]}, "x": 1}
    assert content_hash(a) == content_hash(b)


def test_equal_json_numbers_hash_identically():
    base = {"v": 1}
    for equal in ({"v": 1.0}, {"v": 1e0}, {"v": 100e-2}):
        assert content_hash(equal) == content_hash(base)
    assert content_hash({"v": -0.0}) == content_hash({"v": 0})
    assert content_hash({"v": 1500.0}) == content_hash({"v": 1.5e3})


def test_different_values_hash_differently():
    assert content_hash({"v": 1}) != content_hash({"v": 1.0000001})
    assert content_hash({"v": 0.1}) != content_hash({"v": 0.2})


def test_types_that_json_distinguishes_are_not_conflated():
    assert content_hash({"v": 1}) != content_hash({"v": True})
    assert content_hash({"v": 1}) != content_hash({"v": "1"})
    assert content_hash({"v": None}) != content_hash({"v": 0})


def test_array_order_is_significant():
    assert content_hash({"m": [1, 2]}) != content_hash({"m": [2, 1]})
    e = make_envelope()
    swapped = {**e, "metrics": list(reversed(e["metrics"]))}
    assert content_hash(e) != content_hash(swapped)


def test_canonical_form_is_compact_sorted_and_keeps_unicode():
    assert canonical_json({"b": "é", "a": 2.5}) == '{"a":2.5,"b":"é"}'
