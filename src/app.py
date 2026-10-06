import datetime as dt
import logging
import re
from zoneinfo import ZoneInfo

import bedrock_extractor
import calendar_client
import config
import connpass_ics
import dedupe_store
import gmail_client
import google_secrets

log = logging.getLogger()
log.setLevel(logging.INFO)

_DATE_ONLY = re.compile(r"\d{4}-\d{2}-\d{2}")

# A start time without an offset is read in the zone calendar_client registers it in.
_NAIVE_START_TIMEZONE = ZoneInfo(calendar_client.DEFAULT_TIMEZONE)


def _parse_moment(value: str) -> dt.datetime:
    moment = dt.datetime.fromisoformat(value)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=_NAIVE_START_TIMEZONE)
    return moment


def _finishes_at(extracted) -> dt.datetime:
    """When the event is over: its end if that is readable and after the
    start, otherwise its start. Raises ValueError for an unreadable start."""
    start = _parse_moment(extracted.start_datetime)
    if _DATE_ONLY.fullmatch(extracted.start_datetime):
        start += dt.timedelta(days=1)  # an all-day start covers the whole day
    if not extracted.end_datetime:
        return start
    try:
        end = _parse_moment(extracted.end_datetime)
    except ValueError:
        return start
    return max(start, end)


def _recorded_unless_upcoming(
    cfg, dedupe_key: str, extracted, results: dict, now: dt.datetime | None
) -> bool:
    """Records an event whose start is unreadable (error) or that is already
    over (skip) and returns True; returns False when it has yet to finish. A
    finished event must never reach the calendar API, which would email an
    invitation to every attendee for something that has already happened."""
    try:
        finishes_at = _finishes_at(extracted)
    except ValueError as exc:
        log.warning("Unparseable start_datetime for %s", dedupe_key)
        dedupe_store.mark_error(cfg.table_name, dedupe_key, extracted, str(exc))
        results["errors"] += 1
        return True
    if finishes_at < (now or dt.datetime.now(dt.timezone.utc)):
        log.info(
            "Skipping past event %s (start=%s)", dedupe_key, extracted.start_datetime
        )
        dedupe_store.mark_skipped(cfg.table_name, dedupe_key, extracted, reason="past_event")
        results["skipped"] += 1
        return True
    return False


def _register_event(
    cfg, personal_creds, dedupe_key, extracted, results, now: dt.datetime | None = None
) -> None:
    """Confidence/start-datetime gates, dry-run branch, calendar registration,
    and dedupe recording — shared by both the Gmail+Bedrock candidates and
    connpass ICS candidates."""
    if not extracted.is_event_confirmation or extracted.confidence < cfg.confidence_threshold:
        dedupe_store.mark_skipped(
            cfg.table_name, dedupe_key, extracted, reason="low_confidence_or_not_event"
        )
        results["skipped"] += 1
        return

    if not extracted.start_datetime:
        dedupe_store.mark_skipped(
            cfg.table_name, dedupe_key, extracted, reason="missing_start_datetime"
        )
        results["skipped"] += 1
        return

    if _recorded_unless_upcoming(cfg, dedupe_key, extracted, results, now):
        return

    if cfg.dry_run:
        log.info("DRY_RUN: would register calendar event for %r", extracted)
        results["processed"] += 1
        return

    try:
        personal_event_id = calendar_client.create_personal_event(
            personal_creds,
            cfg.personal_calendar_id,
            extracted,
            cfg.attendee_emails,
            cfg.event_visibility,
        )
        dedupe_store.mark_processed(cfg.table_name, dedupe_key, extracted, personal_event_id)
        results["processed"] += 1
    except Exception as exc:  # noqa: BLE001 — recorded per-message, loop must continue
        log.exception("Failed to register event for %s", dedupe_key)
        dedupe_store.mark_error(cfg.table_name, dedupe_key, extracted, str(exc))
        results["errors"] += 1


def lambda_handler(event, context):
    cfg = config.load()

    personal_creds = google_secrets.load_google_credentials(
        cfg.secret_arn_personal, google_secrets.SCOPES_PERSONAL
    )

    results = {"processed": 0, "skipped": 0, "errors": 0}

    if cfg.connpass_ics_secret_arn:
        ics_url = connpass_ics.load_ics_url(cfg.connpass_ics_secret_arn)
        if ics_url is None:
            log.info("connpass ICS secret is still a placeholder; skipping")
        else:
            try:
                ics_events = connpass_ics.fetch_events(ics_url)
            except Exception:  # noqa: BLE001 — a feed outage must not abort Gmail processing
                log.exception("Failed to fetch/parse connpass ICS feed")
                ics_events = []
            log.info("connpass ICS feed returned %d event(s)", len(ics_events))
            for uid, extracted in ics_events:
                dedupe_key = f"connpass-ics:{uid}"
                if dedupe_store.is_processed(cfg.table_name, dedupe_key):
                    continue
                _register_event(cfg, personal_creds, dedupe_key, extracted, results)

    gmail = gmail_client.build_service(personal_creds)
    query = gmail_client.build_query(cfg)
    candidate_ids = gmail_client.search(gmail, query)
    log.info("Gmail query %r matched %d message(s)", query, len(candidate_ids))

    for message_id in candidate_ids:
        if dedupe_store.is_processed(cfg.table_name, message_id):
            continue

        try:
            body_text, sender_domain = gmail_client.fetch_and_decode(gmail, message_id)
            extracted = bedrock_extractor.extract(body_text, sender_domain, cfg.bedrock_model_id)
        except Exception as exc:  # noqa: BLE001 — a single bad message must not abort the batch
            log.exception("Failed to extract event details for message %s", message_id)
            dedupe_store.mark_error(cfg.table_name, message_id, None, str(exc))
            results["errors"] += 1
            continue

        _register_event(cfg, personal_creds, message_id, extracted, results)

    log.info("Run complete: %s", results)
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(lambda_handler({}, None))
