import json

import boto3
import pytest
from moto import mock_aws

from reauth_app import SCOPES, derive_state_signing_key, handle_request, lambda_handler

SHARED_SECRET = "shared-secret-value"
WEB_CLIENT_ID = "web-client-id"
WEB_CLIENT_SECRET = "web-client-secret"
OWNER_EMAIL = "owner@example.com"
DOMAIN_NAME = "abc123.lambda-url.ap-northeast-1.on.aws"


def _valid_state(now=1_000_000.0):
    from reauth_state import sign_state

    return sign_state(derive_state_signing_key(SHARED_SECRET), now=now)


def _base_kwargs(**overrides):
    kwargs = dict(
        shared_secret=SHARED_SECRET,
        web_client_id=WEB_CLIENT_ID,
        web_client_secret=WEB_CLIENT_SECRET,
        expected_owner_email=OWNER_EMAIL,
        put_secret=lambda payload: None,
    )
    kwargs.update(overrides)
    return kwargs


# --- start flow ---


def test_start_flow_rejects_missing_key():
    response = handle_request({}, DOMAIN_NAME, **_base_kwargs())

    assert response["statusCode"] == 403


def test_start_flow_rejects_wrong_key():
    response = handle_request({"key": "wrong"}, DOMAIN_NAME, **_base_kwargs())

    assert response["statusCode"] == 403


def test_start_flow_redirects_to_google_with_signed_state():
    response = handle_request(
        {"key": SHARED_SECRET}, DOMAIN_NAME, **_base_kwargs(), now=1_000_000.0
    )

    assert response["statusCode"] == 302
    location = response["headers"]["Location"]
    assert location.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert f"redirect_uri=https%3A%2F%2F{DOMAIN_NAME}%2F" in location
    assert "state=" in location
    for scope in SCOPES:
        assert scope.replace(":", "%3A").replace("/", "%2F") in location or scope in location


def test_start_flow_rejects_non_get_method():
    response = handle_request(
        {"key": SHARED_SECRET},
        DOMAIN_NAME,
        **_base_kwargs(),
        now=1_000_000.0,
        http_method="POST",
    )

    assert response["statusCode"] == 405


def test_responses_disable_caching_and_referrer_leakage():
    response = handle_request({}, DOMAIN_NAME, **_base_kwargs())

    assert response["headers"]["Cache-Control"] == "no-store"
    assert response["headers"]["Referrer-Policy"] == "no-referrer"


# --- callback flow: error / invalid state ---


def test_callback_flow_reports_google_denied_consent():
    response = handle_request(
        {"error": "access_denied"}, DOMAIN_NAME, **_base_kwargs()
    )

    assert response["statusCode"] == 400


def test_callback_flow_rejects_missing_state():
    response = handle_request({"code": "auth-code"}, DOMAIN_NAME, **_base_kwargs())

    assert response["statusCode"] == 400


def test_callback_flow_rejects_invalid_state():
    response = handle_request(
        {"code": "auth-code", "state": "garbage"}, DOMAIN_NAME, **_base_kwargs()
    )

    assert response["statusCode"] == 400


def test_callback_flow_rejects_expired_state():
    state = _valid_state(now=1_000_000.0)

    response = handle_request(
        {"code": "auth-code", "state": state},
        DOMAIN_NAME,
        **_base_kwargs(),
        now=1_000_601.0,
    )

    assert response["statusCode"] == 400


# --- callback flow: token exchange / identity checks ---


def test_callback_flow_surfaces_token_exchange_failure_without_writing_secret():
    from reauth_oauth import TokenExchangeError

    state = _valid_state()
    put_calls = []

    def failing_exchange(**kwargs):
        raise TokenExchangeError("boom")

    response = handle_request(
        {"code": "auth-code", "state": state},
        DOMAIN_NAME,
        **_base_kwargs(put_secret=lambda payload: put_calls.append(payload)),
        now=1_000_000.0,
        exchange_code_for_tokens=failing_exchange,
    )

    assert response["statusCode"] == 502
    assert put_calls == []


def test_callback_flow_surfaces_missing_refresh_token_without_writing_secret():
    from reauth_oauth import MissingRefreshTokenError

    state = _valid_state()
    put_calls = []

    def failing_exchange(**kwargs):
        raise MissingRefreshTokenError("no refresh token")

    response = handle_request(
        {"code": "auth-code", "state": state},
        DOMAIN_NAME,
        **_base_kwargs(put_secret=lambda payload: put_calls.append(payload)),
        now=1_000_000.0,
        exchange_code_for_tokens=failing_exchange,
    )

    assert response["statusCode"] == 400
    assert put_calls == []


def test_callback_flow_rejects_non_get_method():
    state = _valid_state()

    response = handle_request(
        {"code": "auth-code", "state": state},
        DOMAIN_NAME,
        **_base_kwargs(),
        now=1_000_000.0,
        http_method="POST",
    )

    assert response["statusCode"] == 405


def test_callback_flow_rejects_unverifiable_id_token_without_writing_secret():
    state = _valid_state()
    put_calls = []

    def fake_exchange(**kwargs):
        return {"refresh_token": "rt", "id_token": "idt"}

    def failing_verify_id_token(id_token_str, audience):
        raise ValueError("token signature invalid")

    response = handle_request(
        {"code": "auth-code", "state": state},
        DOMAIN_NAME,
        **_base_kwargs(put_secret=lambda payload: put_calls.append(payload)),
        now=1_000_000.0,
        exchange_code_for_tokens=fake_exchange,
        verify_id_token=failing_verify_id_token,
    )

    assert response["statusCode"] == 403
    assert put_calls == []


def test_callback_flow_rejects_wrong_google_account_without_writing_secret():
    state = _valid_state()
    put_calls = []

    def fake_exchange(**kwargs):
        return {"refresh_token": "rt", "id_token": "idt"}

    def fake_verify_id_token(id_token_str, audience):
        return {"email": "attacker@example.com", "email_verified": True}

    response = handle_request(
        {"code": "auth-code", "state": state},
        DOMAIN_NAME,
        **_base_kwargs(put_secret=lambda payload: put_calls.append(payload)),
        now=1_000_000.0,
        exchange_code_for_tokens=fake_exchange,
        verify_id_token=fake_verify_id_token,
    )

    assert response["statusCode"] == 403
    assert put_calls == []


def test_callback_flow_writes_secret_and_succeeds_for_the_owner_account():
    state = _valid_state()
    put_calls = []

    def fake_exchange(**kwargs):
        return {"refresh_token": "new-refresh-token", "id_token": "idt"}

    def fake_verify_id_token(id_token_str, audience):
        return {"email": OWNER_EMAIL, "email_verified": True}

    response = handle_request(
        {"code": "auth-code", "state": state},
        DOMAIN_NAME,
        **_base_kwargs(put_secret=lambda payload: put_calls.append(payload)),
        now=1_000_000.0,
        exchange_code_for_tokens=fake_exchange,
        verify_id_token=fake_verify_id_token,
    )

    assert response["statusCode"] == 200
    [payload] = put_calls
    assert payload == {
        "client_id": WEB_CLIENT_ID,
        "client_secret": WEB_CLIENT_SECRET,
        "refresh_token": "new-refresh-token",
        "token_uri": "https://oauth2.googleapis.com/token",
    }


def test_callback_flow_returns_generic_error_on_unexpected_exception_without_writing_secret():
    state = _valid_state()
    put_calls = []

    def exploding_exchange(**kwargs):
        raise RuntimeError("boom: something with a sensitive-looking detail")

    response = handle_request(
        {"code": "auth-code", "state": state},
        DOMAIN_NAME,
        **_base_kwargs(put_secret=lambda payload: put_calls.append(payload)),
        now=1_000_000.0,
        exchange_code_for_tokens=exploding_exchange,
    )

    assert response["statusCode"] == 502
    assert "boom" not in response["body"]
    assert "RuntimeError" not in response["body"]
    assert put_calls == []


# --- lambda_handler wiring ---


@mock_aws
def test_lambda_handler_wires_env_and_secrets_end_to_end(monkeypatch):
    secrets_client = boto3.client("secretsmanager", region_name="ap-northeast-1")
    secrets_client.create_secret(Name="personal-google", SecretString="{}")
    secrets_client.create_secret(
        Name="web-oauth-client",
        SecretString=json.dumps({"client_id": WEB_CLIENT_ID, "client_secret": WEB_CLIENT_SECRET}),
    )
    secrets_client.create_secret(Name="shared-secret", SecretString=SHARED_SECRET)

    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-1")
    monkeypatch.setenv("SECRET_NAME_PERSONAL_GOOGLE", "personal-google")
    monkeypatch.setenv("SECRET_NAME_WEB_OAUTH_CLIENT", "web-oauth-client")
    monkeypatch.setenv("SECRET_NAME_SHARED_SECRET", "shared-secret")
    monkeypatch.setenv("EXPECTED_OWNER_EMAIL", OWNER_EMAIL)

    event = {
        "queryStringParameters": {"key": "wrong-key"},
        "requestContext": {"domainName": DOMAIN_NAME, "http": {"method": "GET"}},
    }

    response = lambda_handler(event, None)

    assert response["statusCode"] == 403


@mock_aws
def test_lambda_handler_success_path_writes_only_the_personal_google_secret(monkeypatch):
    import reauth_app

    secrets_client = boto3.client("secretsmanager", region_name="ap-northeast-1")
    secrets_client.create_secret(Name="personal-google", SecretString="{}")
    secrets_client.create_secret(
        Name="web-oauth-client",
        SecretString=json.dumps({"client_id": WEB_CLIENT_ID, "client_secret": WEB_CLIENT_SECRET}),
    )
    secrets_client.create_secret(Name="shared-secret", SecretString=SHARED_SECRET)

    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-1")
    monkeypatch.setenv("SECRET_NAME_PERSONAL_GOOGLE", "personal-google")
    monkeypatch.setenv("SECRET_NAME_WEB_OAUTH_CLIENT", "web-oauth-client")
    monkeypatch.setenv("SECRET_NAME_SHARED_SECRET", "shared-secret")
    monkeypatch.setenv("EXPECTED_OWNER_EMAIL", OWNER_EMAIL)

    state = _valid_state(now=1_000_000.0)
    monkeypatch.setattr(
        reauth_app,
        "_default_exchange_code_for_tokens",
        lambda **kwargs: {"refresh_token": "new-refresh-token", "id_token": "idt"},
    )
    monkeypatch.setattr(
        reauth_app,
        "_default_verify_id_token",
        lambda id_token_str, audience: {"email": OWNER_EMAIL, "email_verified": True},
    )
    monkeypatch.setattr("reauth_state.time.time", lambda: 1_000_000.0)

    event = {
        "queryStringParameters": {"code": "auth-code", "state": state},
        "requestContext": {"domainName": DOMAIN_NAME, "http": {"method": "GET"}},
    }

    response = lambda_handler(event, None)

    assert response["statusCode"] == 200
    updated = json.loads(
        secrets_client.get_secret_value(SecretId="personal-google")["SecretString"]
    )
    assert updated["refresh_token"] == "new-refresh-token"
    assert updated["client_id"] == WEB_CLIENT_ID

    # the OAuth client + shared-secret secrets must be untouched (Get-only, never Put)
    assert secrets_client.get_secret_value(SecretId="web-oauth-client")["SecretString"] == json.dumps(
        {"client_id": WEB_CLIENT_ID, "client_secret": WEB_CLIENT_SECRET}
    )
    assert secrets_client.get_secret_value(SecretId="shared-secret")["SecretString"] == SHARED_SECRET
