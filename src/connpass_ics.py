import logging
import urllib.request

import boto3
from icalendar import Calendar

from bedrock_extractor import ExtractedEvent

log = logging.getLogger(__name__)

_PLACEHOLDER_VALUES = {"", "REPLACE_ME"}
_REQUEST_TIMEOUT_SECONDS = 15

_secrets_client = None


def _get_client():
    global _secrets_client
    if _secrets_client is None:
        _secrets_client = boto3.client("secretsmanager")
    return _secrets_client


def load_ics_url(secret_arn: str) -> str | None:
    """Returns connpass's personal "joined events" iCalendar subscription
    URL. Returns None if the secret is still the deploy-time placeholder
    (i.e. the user hasn't configured it yet), so callers can skip the
    feature cleanly. Normalizes webcal:// to https://."""
    raw = _get_client().get_secret_value(SecretId=secret_arn)["SecretString"].strip()
    if raw in _PLACEHOLDER_VALUES:
        return None
    if raw.startswith("webcal://"):
        raw = "https://" + raw[len("webcal://"):]
    return raw


def fetch_events(url: str) -> list[tuple[str, ExtractedEvent]]:
    """Fetches and parses the ICS feed. VEVENTs missing a UID or DTSTART are
    skipped with a warning. connpass itself only includes events the user
    has actually joined in this feed, so no Bedrock classification is
    needed: every parsed event is built with is_event_confirmation=True,
    confidence=1.0."""
    calendar = Calendar.from_ical(_fetch_bytes(url))
    events: list[tuple[str, ExtractedEvent]] = []
    for component in calendar.walk("VEVENT"):
        parsed = _parse_vevent(component)
        if parsed is not None:
            events.append(parsed)
    return events


def _fetch_bytes(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "tech-event-calendar-agent"})
    with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:  # noqa: S310
        return response.read()


def _parse_vevent(component) -> tuple[str, ExtractedEvent] | None:
    uid = component.get("uid")
    if not uid:
        log.warning("Skipping connpass ICS VEVENT with no UID: %r", component.get("summary"))
        return None

    dtstart = component.get("dtstart")
    if dtstart is None:
        log.warning("Skipping connpass ICS VEVENT %s with no DTSTART", uid)
        return None

    dtend = component.get("dtend")
    location = component.get("location")
    url_prop = component.get("url")

    event = ExtractedEvent(
        is_event_confirmation=True,
        confidence=1.0,
        title=str(component.get("summary") or ""),
        start_datetime=dtstart.dt.isoformat(),
        end_datetime=dtend.dt.isoformat() if dtend is not None else None,
        location=str(location) if location else None,
        event_url=str(url_prop) if url_prop else None,
    )
    return str(uid), event
