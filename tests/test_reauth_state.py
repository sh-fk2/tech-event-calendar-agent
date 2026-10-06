import pytest

from reauth_state import sign_state, verify_state

SECRET = "test-shared-secret"


def test_verify_state_accepts_a_freshly_signed_state():
    state = sign_state(SECRET, now=1_000_000.0)

    assert verify_state(SECRET, state, max_age_seconds=600, now=1_000_000.0) is True


def test_verify_state_accepts_state_within_max_age():
    state = sign_state(SECRET, now=1_000_000.0)

    assert verify_state(SECRET, state, max_age_seconds=600, now=1_000_599.0) is True


def test_verify_state_rejects_state_older_than_max_age():
    state = sign_state(SECRET, now=1_000_000.0)

    assert verify_state(SECRET, state, max_age_seconds=600, now=1_000_601.0) is False


def test_verify_state_rejects_state_signed_with_a_different_secret():
    state = sign_state("other-secret", now=1_000_000.0)

    assert verify_state(SECRET, state, max_age_seconds=600, now=1_000_000.0) is False


def test_verify_state_rejects_a_tampered_payload():
    state = sign_state(SECRET, now=1_000_000.0)
    payload, nonce, sig = state.split(".")
    tampered = f"{payload}.{nonce}x.{sig}"

    assert verify_state(SECRET, tampered, max_age_seconds=600, now=1_000_000.0) is False


def test_verify_state_rejects_malformed_state():
    assert verify_state(SECRET, "not-a-valid-state", max_age_seconds=600) is False
    assert verify_state(SECRET, "", max_age_seconds=600) is False


def test_sign_state_produces_distinct_states_on_each_call():
    first = sign_state(SECRET, now=1_000_000.0)
    second = sign_state(SECRET, now=1_000_000.0)

    assert first != second
