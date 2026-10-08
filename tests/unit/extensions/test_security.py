import hashlib

from extensions.security import generate_key, hash_key, keys_match


def test_generate_key_returns_unique_url_safe_tokens():
    a = generate_key()
    b = generate_key()
    assert a != b
    assert len(a) >= 32


def test_hash_key_is_deterministic_sha256_hex():
    raw = "some-raw-key"
    assert hash_key(raw) == hashlib.sha256(raw.encode()).hexdigest()


def test_keys_match_true_for_matching_raw_and_hash():
    raw = generate_key()
    assert keys_match(raw, hash_key(raw)) is True


def test_keys_match_false_for_mismatched_raw_and_hash():
    assert keys_match(generate_key(), hash_key(generate_key())) is False


def test_keys_match_false_for_empty_raw_or_hash():
    assert keys_match("", hash_key("anything")) is False
    assert keys_match("anything", "") is False
    assert keys_match("", "") is False
