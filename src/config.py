import os
from dataclasses import dataclass


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(frozen=True)
class Config:
    table_name: str
    secret_arn_personal: str
    personal_calendar_id: str
    attendee_email: str | None
    bedrock_model_id: str
    sender_allowlist: list[str]
    keywords: list[str]
    search_window: str
    confidence_threshold: float
    default_duration_minutes: int
    dry_run: bool


def load() -> Config:
    return Config(
        table_name=os.environ["DDB_TABLE_NAME"],
        secret_arn_personal=os.environ["SECRET_NAME_PERSONAL_GOOGLE"],
        personal_calendar_id=os.environ.get("PERSONAL_CALENDAR_ID", "primary"),
        attendee_email=os.environ.get("ATTENDEE_EMAIL") or None,
        bedrock_model_id=os.environ.get("BEDROCK_MODEL_ID", "amazon.nova-lite-v1:0"),
        sender_allowlist=_split_csv(os.environ.get("GMAIL_SENDER_ALLOWLIST", "")),
        keywords=_split_csv(os.environ.get("GMAIL_KEYWORDS", "")),
        search_window=os.environ.get("GMAIL_SEARCH_WINDOW", "newer_than:3d"),
        confidence_threshold=float(os.environ.get("CONFIDENCE_THRESHOLD", "0.6")),
        default_duration_minutes=int(os.environ.get("DEFAULT_DURATION_MINUTES", "120")),
        dry_run=os.environ.get("DRY_RUN", "false").lower() == "true",
    )
