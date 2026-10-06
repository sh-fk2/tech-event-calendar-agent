from urllib.parse import urlencode

import requests

AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"


class TokenExchangeError(Exception):
    """Google's token endpoint returned a non-200 response."""


class MissingRefreshTokenError(Exception):
    """Google's token endpoint did not include a refresh_token. Happens if
    the consent screen wasn't actually shown (prompt=consent should prevent
    this) — ask the user to retry."""


def build_authorization_url(
    *, client_id: str, redirect_uri: str, scopes: list[str], state: str
) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{AUTHORIZATION_ENDPOINT}?{urlencode(params)}"


def exchange_code_for_tokens(
    *,
    code: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    http_post=requests.post,
) -> dict:
    response = http_post(
        TOKEN_ENDPOINT,
        data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=10,
    )
    if response.status_code != 200:
        raise TokenExchangeError(f"token exchange failed ({response.status_code}): {response.text}")

    payload = response.json()
    if not payload.get("refresh_token"):
        raise MissingRefreshTokenError("no refresh_token in token response")
    return payload


def verify_owner_email(claims: dict, expected_email: str) -> bool:
    return claims.get("email_verified") is True and claims.get("email") == expected_email
