import json

import boto3
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

SCOPES_PERSONAL = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.events",
]

_secrets_client = None


def _get_client():
    global _secrets_client
    if _secrets_client is None:
        _secrets_client = boto3.client("secretsmanager")
    return _secrets_client


def load_google_credentials(secret_arn: str, scopes: list[str]) -> Credentials:
    """Load OAuth client + refresh token from Secrets Manager and refresh it
    into a usable access token. Only a refresh token is ever persisted; a
    fresh access token is minted on every Lambda invocation."""
    raw = _get_client().get_secret_value(SecretId=secret_arn)["SecretString"]
    payload = json.loads(raw)

    creds = Credentials(
        token=None,
        refresh_token=payload["refresh_token"],
        token_uri=payload.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=payload["client_id"],
        client_secret=payload["client_secret"],
        scopes=scopes,
    )
    creds.refresh(Request())
    return creds
