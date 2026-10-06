import datetime as dt
import logging

from googleapiclient.discovery import build

from bedrock_extractor import ExtractedEvent

log = logging.getLogger(__name__)

DEFAULT_TIMEZONE = "Asia/Tokyo"


def build_service(credentials):
    return build("calendar", "v3", credentials=credentials, cache_discovery=False)


def create_personal_event(
    credentials,
    calendar_id: str,
    event: ExtractedEvent,
    attendee_emails: list[str] | None = None,
    event_visibility: str = "default",
) -> str:
    service = build_service(credentials)
    start, end = _resolve_start_end(event, default_duration_minutes=120)
    attendee_emails = attendee_emails or []

    body = {
        "summary": event.title,
        "location": event.location or None,
        "description": event.event_url or None,
        "start": start,
        "end": end,
    }
    if attendee_emails:
        body["attendees"] = [{"email": email} for email in attendee_emails]
    if event_visibility == "private":
        body["visibility"] = "private"

    send_updates = "all" if attendee_emails else "none"
    created = service.events().insert(
        calendarId=calendar_id, body=body, sendUpdates=send_updates
    ).execute()
    return created["id"]


def _resolve_start_end(event: ExtractedEvent, default_duration_minutes: int) -> tuple[dict, dict]:
    """Returns Google Calendar API start/end objects. A naive (no-tzinfo)
    datetime from the extractor gets an explicit timeZone field instead of
    relying on an offset being present in the string."""
    start_dt = dt.datetime.fromisoformat(event.start_datetime)
    end_dt = dt.datetime.fromisoformat(event.end_datetime) if event.end_datetime else (
        start_dt + dt.timedelta(minutes=default_duration_minutes)
    )

    start: dict = {"dateTime": start_dt.isoformat()}
    end: dict = {"dateTime": end_dt.isoformat()}
    if start_dt.tzinfo is None:
        start["timeZone"] = DEFAULT_TIMEZONE
    if end_dt.tzinfo is None:
        end["timeZone"] = DEFAULT_TIMEZONE
    return start, end
