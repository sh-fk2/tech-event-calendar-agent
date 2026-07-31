"""One-time local helper to mint a Google OAuth refresh token and push it
into the AWS Secrets Manager secret used by the personal Gmail/Calendar
account.

Run this once:

    python scripts/generate_refresh_token.py \\
        --client-secrets-file ~/Downloads/client_secret_personal.json \\
        --secret-name tech-event-agent/google-personal

--client-secrets-file is the OAuth client JSON downloaded from the Google
Cloud Console (APIs & Services -> Credentials -> OAuth 2.0 Client IDs ->
Download JSON). This opens a browser window for you to log in with the
matching Google account and grant consent; the resulting refresh token is
then written to the named Secrets Manager secret, overwriting its
REPLACE_ME placeholder.

Before running: make sure the OAuth consent screen for this client is set
to "In production" (not "Testing"), otherwise the refresh token expires
after 7 days and the poller will silently start failing.
"""

import argparse
import json

import boto3
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.events",
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-secrets-file", required=True)
    parser.add_argument("--secret-name", required=True, help="Secrets Manager secret name or ARN")
    args = parser.parse_args()

    flow = InstalledAppFlow.from_client_secrets_file(args.client_secrets_file, SCOPES)
    credentials = flow.run_local_server(port=0)

    if not credentials.refresh_token:
        raise SystemExit(
            "No refresh_token returned. Revoke the app's existing access at "
            "https://myaccount.google.com/permissions and re-run this script "
            "so Google issues a fresh consent grant."
        )

    payload = {
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
    }

    secrets_client = boto3.client("secretsmanager")
    secrets_client.put_secret_value(SecretId=args.secret_name, SecretString=json.dumps(payload))
    print(f"Refresh token written to {args.secret_name}")


if __name__ == "__main__":
    main()
