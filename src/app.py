import logging

import bedrock_extractor
import calendar_client
import config
import dedupe_store
import gmail_client
import google_secrets

log = logging.getLogger()
log.setLevel(logging.INFO)


def lambda_handler(event, context):
    cfg = config.load()

    personal_creds = google_secrets.load_google_credentials(
        cfg.secret_arn_personal, google_secrets.SCOPES_PERSONAL
    )

    gmail = gmail_client.build_service(personal_creds)
    query = gmail_client.build_query(cfg)
    candidate_ids = gmail_client.search(gmail, query)
    log.info("Gmail query %r matched %d message(s)", query, len(candidate_ids))

    results = {"processed": 0, "skipped": 0, "errors": 0}

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

        if not extracted.is_event_confirmation or extracted.confidence < cfg.confidence_threshold:
            dedupe_store.mark_skipped(
                cfg.table_name, message_id, extracted, reason="low_confidence_or_not_event"
            )
            results["skipped"] += 1
            continue

        if not extracted.start_datetime:
            dedupe_store.mark_skipped(
                cfg.table_name, message_id, extracted, reason="missing_start_datetime"
            )
            results["skipped"] += 1
            continue

        if cfg.dry_run:
            log.info("DRY_RUN: would register calendar event for %r", extracted)
            results["processed"] += 1
            continue

        try:
            personal_event_id = calendar_client.create_personal_event(
                personal_creds, cfg.personal_calendar_id, extracted, cfg.attendee_email
            )
            dedupe_store.mark_processed(cfg.table_name, message_id, extracted, personal_event_id)
            results["processed"] += 1
        except Exception as exc:  # noqa: BLE001 — recorded per-message, loop must continue
            log.exception("Failed to register event for message %s", message_id)
            dedupe_store.mark_error(cfg.table_name, message_id, extracted, str(exc))
            results["errors"] += 1

    log.info("Run complete: %s", results)
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(lambda_handler({}, None))
