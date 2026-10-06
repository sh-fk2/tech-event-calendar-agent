import hashlib
import hmac
import secrets
import time


def sign_state(secret: str, *, now: float | None = None) -> str:
    """Build a signed, timestamped anti-CSRF token for the OAuth `state`
    param. Relying party still needs to check `verify_state` before trusting
    a callback; the authorization `code` itself is single-use on Google's
    side, so this signature only needs to resist forgery, not replay."""
    timestamp = str(int(now if now is not None else time.time()))
    nonce = secrets.token_urlsafe(16)
    payload = f"{timestamp}.{nonce}"
    signature = _sign(secret, payload)
    return f"{payload}.{signature}"


def verify_state(
    secret: str, state: str, *, max_age_seconds: int, now: float | None = None
) -> bool:
    parts = state.split(".")
    if len(parts) != 3:
        return False
    timestamp_str, nonce, signature = parts

    payload = f"{timestamp_str}.{nonce}"
    expected_signature = _sign(secret, payload)
    if not hmac.compare_digest(signature, expected_signature):
        return False

    try:
        timestamp = int(timestamp_str)
    except ValueError:
        return False

    current_time = now if now is not None else time.time()
    age = current_time - timestamp
    return 0 <= age <= max_age_seconds


def _sign(secret: str, payload: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
