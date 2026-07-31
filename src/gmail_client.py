import base64
import logging
from email.utils import parseaddr

from googleapiclient.discovery import build

from config import Config

log = logging.getLogger(__name__)

_MAX_RESULTS = 50
_MAX_BODY_CHARS = 6000


def build_service(credentials):
    return build("gmail", "v1", credentials=credentials, cache_discovery=False)


def build_query(cfg: Config) -> str:
    """Sender-domain allowlist OR keyword match, bounded by the search
    window. Either list may be empty (e.g. keyword-only or domain-only)."""
    clauses = []
    if cfg.sender_allowlist:
        clauses.extend(f"from:{domain}" for domain in cfg.sender_allowlist)
    if cfg.keywords:
        clauses.extend(f'"{kw}"' for kw in cfg.keywords)

    if not clauses:
        raise ValueError("Both GMAIL_SENDER_ALLOWLIST and GMAIL_KEYWORDS are empty")

    return f"({' OR '.join(clauses)}) {cfg.search_window}"


def search(service, query: str) -> list[str]:
    message_ids: list[str] = []
    request = service.users().messages().list(userId="me", q=query, maxResults=_MAX_RESULTS)
    while request is not None:
        response = request.execute()
        message_ids.extend(msg["id"] for msg in response.get("messages", []))
        request = service.users().messages().list_next(request, response)
    return message_ids


def fetch_and_decode(service, message_id: str) -> tuple[str, str]:
    """Returns (plain_text_body, sender_domain)."""
    message = service.users().messages().get(userId="me", id=message_id, format="full").execute()

    headers = {h["name"].lower(): h["value"] for h in message["payload"].get("headers", [])}
    _, sender_email = parseaddr(headers.get("from", ""))
    sender_domain = sender_email.split("@")[-1].lower() if "@" in sender_email else ""

    body_text = _extract_plain_text(message["payload"]) or message.get("snippet", "")
    return body_text[:_MAX_BODY_CHARS], sender_domain


def _extract_plain_text(payload: dict) -> str:
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return _decode_body(payload["body"]["data"])

    for part in payload.get("parts", []):
        text = _extract_plain_text(part)
        if text:
            return text
    return ""


def _decode_body(data: str) -> str:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")
