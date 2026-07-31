import datetime as dt

import boto3

from bedrock_extractor import ExtractedEvent

_TTL_DAYS = 180

_dynamodb = None


def _get_resource():
    global _dynamodb
    if _dynamodb is None:
        _dynamodb = boto3.resource("dynamodb")
    return _dynamodb


def _table(table_name: str):
    return _get_resource().Table(table_name)


def is_processed(table_name: str, message_id: str) -> bool:
    response = _table(table_name).get_item(Key={"gmail_message_id": message_id})
    return "Item" in response


def _base_item(message_id: str, event: ExtractedEvent | None) -> dict:
    now = dt.datetime.now(dt.timezone.utc)
    item = {
        "gmail_message_id": message_id,
        "processed_at": now.isoformat(),
        "ttl": int((now + dt.timedelta(days=_TTL_DAYS)).timestamp()),
    }
    if event is not None:
        item.update(
            {
                "event_title": event.title,
                "event_start": event.start_datetime or "",
                "event_end": event.end_datetime or "",
                "location": event.location or "",
                "confidence": str(event.confidence),
            }
        )
    return item


def mark_processed(
    table_name: str,
    message_id: str,
    event: ExtractedEvent,
    personal_calendar_event_id: str,
) -> None:
    item = _base_item(message_id, event)
    item["status"] = "PROCESSED"
    item["personal_calendar_event_id"] = personal_calendar_event_id
    _table(table_name).put_item(Item=item)


def mark_skipped(table_name: str, message_id: str, event: ExtractedEvent, reason: str) -> None:
    item = _base_item(message_id, event)
    item["status"] = f"SKIPPED_{reason.upper()}"
    _table(table_name).put_item(Item=item)


def mark_error(
    table_name: str, message_id: str, event: ExtractedEvent | None, error_message: str
) -> None:
    item = _base_item(message_id, event)
    item["status"] = "ERROR"
    item["error_message"] = error_message[:1000]
    _table(table_name).put_item(Item=item)
