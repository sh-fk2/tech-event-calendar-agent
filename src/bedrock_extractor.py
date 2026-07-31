import datetime as dt
import logging
from dataclasses import dataclass

import boto3

log = logging.getLogger(__name__)

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = boto3.client("bedrock-runtime")
    return _client


_TOOL_NAME = "extract_event"

_TOOL_CONFIG = {
    "tools": [
        {
            "toolSpec": {
                "name": _TOOL_NAME,
                "description": (
                    "Extract structured event details from a reservation/registration "
                    "confirmation email, if the email is one."
                ),
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": {
                            "is_event_confirmation": {"type": "boolean"},
                            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                            "title": {"type": "string"},
                            "start_datetime": {
                                "type": ["string", "null"],
                                "description": "ISO8601 with timezone offset",
                            },
                            "end_datetime": {"type": ["string", "null"]},
                            "location": {"type": ["string", "null"]},
                            "event_url": {"type": ["string", "null"]},
                        },
                        "required": ["is_event_confirmation", "confidence", "title", "start_datetime"],
                    }
                },
            }
        }
    ],
    "toolChoice": {"tool": {"name": _TOOL_NAME}},
}


def _system_prompt(today: str) -> str:
    return (
        "You read an email from a Japanese or English event ticketing/registration "
        "platform (e.g. connpass, Peatix, TECH PLAY, Doorkeeper, Eventbrite). Call "
        "extract_event exactly once.\n\n"
        "Set is_event_confirmation=true ONLY if the email explicitly confirms that "
        "THE RECIPIENT PERSONALLY just completed a reservation/registration/"
        "application for a specific dated event (e.g. '予約が完了しました', "
        "'参加申し込みが完了しました', 'your registration is confirmed', 'you have "
        "registered'). The email must be about the recipient's own completed action, "
        "not merely describe or promote the event.\n\n"
        "Set is_event_confirmation=false for everything else, even if it states the "
        "event's date/time/location in full detail, including:\n"
        "- New event announcements or publications by an organizer/group (e.g. "
        "'〜がイベントを公開しました', 'a new event was published') — the recipient did "
        "not register, someone is just promoting the event\n"
        "- Reminders about an upcoming event or a closing registration window (e.g. "
        "'明日開催です', 'is tomorrow', '募集終了は明日です', 'registration closes "
        "tomorrow')\n"
        "- Broadcast messages from event organizers or community/group admins to "
        "members (e.g. 'イベント管理者からのメッセージ', 'グループ管理者からのメッセージ', "
        "'message from the organizer')\n"
        "- Newsletters, event digests, or recommended-events emails\n"
        "- Unrelated receipts, password resets, or generic marketing\n\n"
        "For all false cases, call extract_event with is_event_confirmation=false, "
        'confidence=0, title="", and start_datetime=null.\n\n'
        "Assume Asia/Tokyo timezone unless the email states otherwise. Today's date "
        f"is {today} — resolve any relative dates against it."
    )


@dataclass(frozen=True)
class ExtractedEvent:
    is_event_confirmation: bool
    confidence: float
    title: str
    start_datetime: str | None
    end_datetime: str | None
    location: str | None
    event_url: str | None


def extract(body_text: str, sender_domain: str, model_id: str) -> ExtractedEvent:
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    user_message = f"Sender domain: {sender_domain}\n\nEmail body:\n{body_text}"

    response = _get_client().converse(
        modelId=model_id,
        system=[{"text": _system_prompt(today)}],
        messages=[{"role": "user", "content": [{"text": user_message}]}],
        toolConfig=_TOOL_CONFIG,
    )

    tool_input = _find_tool_input(response)
    return ExtractedEvent(
        is_event_confirmation=bool(tool_input.get("is_event_confirmation", False)),
        confidence=float(tool_input.get("confidence", 0.0)),
        title=tool_input.get("title") or "",
        start_datetime=tool_input.get("start_datetime"),
        end_datetime=tool_input.get("end_datetime"),
        location=tool_input.get("location"),
        event_url=tool_input.get("event_url"),
    )


def _find_tool_input(response: dict) -> dict:
    content_blocks = response["output"]["message"]["content"]
    for block in content_blocks:
        tool_use = block.get("toolUse")
        if tool_use and tool_use.get("name") == _TOOL_NAME:
            return tool_use.get("input", {})
    log.warning("Bedrock response had no %s tool call: %r", _TOOL_NAME, response)
    return {}
