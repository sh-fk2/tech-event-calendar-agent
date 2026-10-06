import hashlib
import hmac
import json
import logging
import os

from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import id_token as google_id_token

from reauth_oauth import (
    MissingRefreshTokenError,
    TokenExchangeError,
    build_authorization_url,
    exchange_code_for_tokens as _default_exchange_code_for_tokens,
    verify_owner_email,
)
from reauth_state import sign_state, verify_state

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.events",
    "openid",
    "email",
]

TOKEN_URI = "https://oauth2.googleapis.com/token"
STATE_MAX_AGE_SECONDS = 600
SECURITY_HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}

_secrets_client = None


def _get_secrets_client():
    global _secrets_client
    if _secrets_client is None:
        import boto3

        _secrets_client = boto3.client("secretsmanager")
    return _secrets_client


def derive_state_signing_key(shared_secret: str) -> str:
    """Separate the CSRF state-signing key from the bearer `key` gate value
    so leaking one doesn't help forge the other."""
    return hashlib.sha256(f"{shared_secret}:state-signing".encode()).hexdigest()


def _default_verify_id_token(id_token_str: str, audience: str) -> dict:
    return google_id_token.verify_oauth2_token(id_token_str, GoogleAuthRequest(), audience=audience)


def _response(status_code: int, body: str, *, extra_headers: dict | None = None) -> dict:
    headers = {"Content-Type": "text/html; charset=utf-8", **SECURITY_HEADERS}
    if extra_headers:
        headers.update(extra_headers)
    return {"statusCode": status_code, "headers": headers, "body": body}


def handle_request(
    query_params: dict,
    domain_name: str,
    *,
    shared_secret: str,
    web_client_id: str,
    web_client_secret: str,
    expected_owner_email: str,
    put_secret,
    now: float | None = None,
    http_method: str = "GET",
    exchange_code_for_tokens=None,
    verify_id_token=None,
) -> dict:
    if http_method != "GET":
        return _response(405, "Method not allowed.")

    try:
        return _route(
            query_params or {},
            domain_name,
            shared_secret=shared_secret,
            web_client_id=web_client_id,
            web_client_secret=web_client_secret,
            expected_owner_email=expected_owner_email,
            put_secret=put_secret,
            now=now,
            exchange_code_for_tokens=exchange_code_for_tokens or _default_exchange_code_for_tokens,
            verify_id_token=verify_id_token or _default_verify_id_token,
        )
    except Exception:
        # Never let an unhandled exception (with its message/traceback) reach
        # this publicly reachable, unauthenticated endpoint. Full details
        # still go to CloudWatch via logger.exception.
        logger.exception("reauth: unhandled error")
        return _response(502, "Unexpected error. Please retry from the start URL.")


def _route(
    query_params,
    domain_name,
    *,
    shared_secret,
    web_client_id,
    web_client_secret,
    expected_owner_email,
    put_secret,
    now,
    exchange_code_for_tokens,
    verify_id_token,
):
    redirect_uri = f"https://{domain_name}/"

    if "error" in query_params:
        logger.warning("reauth: google reported an error outcome")
        return _response(400, "Google consent was cancelled or denied. Reopen the start URL to retry.")

    if "code" in query_params:
        return _handle_callback(
            query_params,
            redirect_uri,
            shared_secret=shared_secret,
            web_client_id=web_client_id,
            web_client_secret=web_client_secret,
            expected_owner_email=expected_owner_email,
            put_secret=put_secret,
            now=now,
            exchange_code_for_tokens=exchange_code_for_tokens,
            verify_id_token=verify_id_token,
        )

    return _handle_start(
        query_params,
        redirect_uri,
        shared_secret=shared_secret,
        web_client_id=web_client_id,
        now=now,
    )


def _handle_start(query_params, redirect_uri, *, shared_secret, web_client_id, now):
    key = query_params.get("key", "")
    if not hmac.compare_digest(key, shared_secret):
        logger.warning("reauth: rejected start request with invalid key")
        return _response(403, "Forbidden.")

    state = sign_state(derive_state_signing_key(shared_secret), now=now)
    auth_url = build_authorization_url(
        client_id=web_client_id, redirect_uri=redirect_uri, scopes=SCOPES, state=state
    )
    return {"statusCode": 302, "headers": {"Location": auth_url, **SECURITY_HEADERS}, "body": ""}


def _handle_callback(
    query_params,
    redirect_uri,
    *,
    shared_secret,
    web_client_id,
    web_client_secret,
    expected_owner_email,
    put_secret,
    now,
    exchange_code_for_tokens,
    verify_id_token,
):
    state = query_params.get("state")
    if not state or not verify_state(
        derive_state_signing_key(shared_secret), state, max_age_seconds=STATE_MAX_AGE_SECONDS, now=now
    ):
        logger.warning("reauth: rejected callback with invalid or expired state")
        return _response(400, "Invalid or expired request. Reopen the start URL to retry.")

    try:
        tokens = exchange_code_for_tokens(
            code=query_params["code"],
            client_id=web_client_id,
            client_secret=web_client_secret,
            redirect_uri=redirect_uri,
        )
    except MissingRefreshTokenError:
        logger.error("reauth: token exchange did not return a refresh token")
        return _response(400, "Google did not return a refresh token. Reopen the start URL to retry.")
    except TokenExchangeError:
        logger.error("reauth: token exchange failed")
        return _response(502, "Failed to exchange the authorization code with Google.")

    try:
        claims = verify_id_token(tokens["id_token"], audience=web_client_id)
    except Exception:
        logger.warning("reauth: id_token verification failed")
        return _response(403, "Failed to verify the Google account identity.")

    if not verify_owner_email(claims, expected_owner_email):
        logger.warning("reauth: rejected callback from an unauthorized google account")
        return _response(403, "This Google account is not authorized to complete this flow.")

    put_secret(
        {
            "client_id": web_client_id,
            "client_secret": web_client_secret,
            "refresh_token": tokens["refresh_token"],
            "token_uri": TOKEN_URI,
        }
    )
    logger.info("reauth: succeeded")
    return _response(200, "Re-authorization successful. You can close this page.")


def lambda_handler(event, context):
    query_params = event.get("queryStringParameters") or {}
    domain_name = event["requestContext"]["domainName"]
    http_method = event["requestContext"]["http"]["method"]

    secrets_client = _get_secrets_client()
    shared_secret = secrets_client.get_secret_value(
        SecretId=os.environ["SECRET_NAME_SHARED_SECRET"]
    )["SecretString"]
    web_client = json.loads(
        secrets_client.get_secret_value(SecretId=os.environ["SECRET_NAME_WEB_OAUTH_CLIENT"])[
            "SecretString"
        ]
    )

    def put_secret(payload: dict) -> None:
        secrets_client.put_secret_value(
            SecretId=os.environ["SECRET_NAME_PERSONAL_GOOGLE"],
            SecretString=json.dumps(payload),
        )

    return handle_request(
        query_params,
        domain_name,
        shared_secret=shared_secret,
        web_client_id=web_client["client_id"],
        web_client_secret=web_client["client_secret"],
        expected_owner_email=os.environ["EXPECTED_OWNER_EMAIL"],
        put_secret=put_secret,
        http_method=http_method,
    )
