import pytest

from reauth_oauth import (
    MissingRefreshTokenError,
    TokenExchangeError,
    build_authorization_url,
    exchange_code_for_tokens,
    verify_owner_email,
)


def test_build_authorization_url_contains_required_params():
    url = build_authorization_url(
        client_id="client-123",
        redirect_uri="https://example.lambda-url.ap-northeast-1.on.aws/",
        scopes=["scope-a", "scope-b"],
        state="signed-state-value",
    )

    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "client_id=client-123" in url
    assert "redirect_uri=https%3A%2F%2Fexample.lambda-url.ap-northeast-1.on.aws%2F" in url
    assert "scope=scope-a+scope-b" in url
    assert "state=signed-state-value" in url
    assert "access_type=offline" in url
    assert "prompt=consent" in url
    assert "response_type=code" in url


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


def test_exchange_code_for_tokens_returns_payload_on_success():
    fake_payload = {
        "access_token": "at",
        "refresh_token": "rt",
        "id_token": "idt",
        "expires_in": 3600,
        "scope": "a b",
        "token_type": "Bearer",
    }
    calls = []

    def fake_post(url, data, timeout):
        calls.append((url, data, timeout))
        return _FakeResponse(200, fake_payload)

    result = exchange_code_for_tokens(
        code="auth-code",
        client_id="client-123",
        client_secret="client-secret",
        redirect_uri="https://example.com/",
        http_post=fake_post,
    )

    assert result == fake_payload
    [(url, data, timeout)] = calls
    assert url == "https://oauth2.googleapis.com/token"
    assert data["code"] == "auth-code"
    assert data["client_id"] == "client-123"
    assert data["client_secret"] == "client-secret"
    assert data["redirect_uri"] == "https://example.com/"
    assert data["grant_type"] == "authorization_code"


def test_exchange_code_for_tokens_raises_on_non_200_response():
    def fake_post(url, data, timeout):
        return _FakeResponse(400, {"error": "invalid_grant"})

    with pytest.raises(TokenExchangeError):
        exchange_code_for_tokens(
            code="auth-code",
            client_id="client-123",
            client_secret="client-secret",
            redirect_uri="https://example.com/",
            http_post=fake_post,
        )


def test_exchange_code_for_tokens_raises_when_refresh_token_missing():
    def fake_post(url, data, timeout):
        return _FakeResponse(200, {"access_token": "at", "id_token": "idt"})

    with pytest.raises(MissingRefreshTokenError):
        exchange_code_for_tokens(
            code="auth-code",
            client_id="client-123",
            client_secret="client-secret",
            redirect_uri="https://example.com/",
            http_post=fake_post,
        )


def test_verify_owner_email_accepts_matching_verified_email():
    claims = {"email": "owner@example.com", "email_verified": True}

    assert verify_owner_email(claims, "owner@example.com") is True


def test_verify_owner_email_rejects_mismatched_email():
    claims = {"email": "someone-else@example.com", "email_verified": True}

    assert verify_owner_email(claims, "owner@example.com") is False


def test_verify_owner_email_rejects_unverified_email():
    claims = {"email": "owner@example.com", "email_verified": False}

    assert verify_owner_email(claims, "owner@example.com") is False


def test_verify_owner_email_rejects_missing_email_verified_claim():
    claims = {"email": "owner@example.com"}

    assert verify_owner_email(claims, "owner@example.com") is False
