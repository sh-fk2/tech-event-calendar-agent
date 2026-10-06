import datetime as dt
from unittest import mock

import app
import config
from bedrock_extractor import ExtractedEvent

JST = dt.timezone(dt.timedelta(hours=9))
NOW = dt.datetime(2026, 10, 6, 12, 0, tzinfo=JST)


def _cfg(**overrides) -> config.Config:
    values = {
        "table_name": "test-table",
        "secret_arn_personal": "test-secret",
        "connpass_ics_secret_arn": "",
        "personal_calendar_id": "primary",
        "attendee_emails": ["attendee@example.com"],
        "event_visibility": "default",
        "bedrock_model_id": "test-model",
        "sender_allowlist": ["example.com"],
        "keywords": [],
        "search_window": "newer_than:3d",
        "confidence_threshold": 0.6,
        "default_duration_minutes": 120,
        "dry_run": False,
    }
    values.update(overrides)
    return config.Config(**values)


def _event(start_datetime: str | None) -> ExtractedEvent:
    return ExtractedEvent(
        is_event_confirmation=True,
        confidence=0.9,
        title="Test Meetup",
        start_datetime=start_datetime,
        end_datetime=None,
        location="Tokyo",
        event_url="https://example.com/events/1",
    )


def _results() -> dict:
    return {"processed": 0, "skipped": 0, "errors": 0}


def test_register_event_past_start_does_not_create_calendar_event():
    # Arrange
    results = _results()

    # Act
    with (
        mock.patch.object(app.calendar_client, "create_personal_event") as create_event,
        mock.patch.object(app.dedupe_store, "mark_skipped"),
        mock.patch.object(app.dedupe_store, "mark_processed"),
    ):
        app._register_event(
            _cfg(), object(), "msg-1", _event("2026-10-05T10:00:00+09:00"), results, now=NOW
        )

    # Assert
    create_event.assert_not_called()


def test_register_event_past_start_records_skip_with_past_event_reason():
    # Arrange
    event = _event("2026-10-05T10:00:00+09:00")

    # Act
    with (
        mock.patch.object(app.calendar_client, "create_personal_event"),
        mock.patch.object(app.dedupe_store, "mark_skipped") as mark_skipped,
        mock.patch.object(app.dedupe_store, "mark_processed") as mark_processed,
    ):
        app._register_event(_cfg(), object(), "msg-1", event, _results(), now=NOW)

    # Assert
    mark_skipped.assert_called_once_with("test-table", "msg-1", event, reason="past_event")
    mark_processed.assert_not_called()


def test_register_event_past_start_counts_as_skipped_in_results():
    # Arrange
    results = _results()

    # Act
    with (
        mock.patch.object(app.calendar_client, "create_personal_event"),
        mock.patch.object(app.dedupe_store, "mark_skipped"),
    ):
        app._register_event(
            _cfg(), object(), "msg-1", _event("2026-10-05T10:00:00+09:00"), results, now=NOW
        )

    # Assert
    assert results == {"processed": 0, "skipped": 1, "errors": 0}


def test_register_event_naive_start_is_compared_as_asia_tokyo():
    # Arrange: 11:00 JST is before NOW (12:00 JST), but would be in the future if read as UTC
    results = _results()

    # Act
    with (
        mock.patch.object(app.calendar_client, "create_personal_event") as create_event,
        mock.patch.object(app.dedupe_store, "mark_skipped"),
    ):
        app._register_event(
            _cfg(), object(), "msg-1", _event("2026-10-06T11:00:00"), results, now=NOW
        )

    # Assert
    create_event.assert_not_called()
    assert results == {"processed": 0, "skipped": 1, "errors": 0}


def test_register_event_unparseable_start_records_error_without_raising():
    # Arrange
    results = _results()

    # Act
    with (
        mock.patch.object(app.calendar_client, "create_personal_event") as create_event,
        mock.patch.object(app.dedupe_store, "mark_error") as mark_error,
    ):
        app._register_event(
            _cfg(), object(), "msg-1", _event("not-a-datetime"), results, now=NOW
        )

    # Assert
    create_event.assert_not_called()
    mark_error.assert_called_once()
    assert mark_error.call_args.args[:2] == ("test-table", "msg-1")
    assert results == {"processed": 0, "skipped": 0, "errors": 1}


def test_register_event_future_start_creates_event_and_marks_processed():
    # Arrange
    event = _event("2026-10-07T10:00:00+09:00")
    results = _results()

    # Act
    with (
        mock.patch.object(
            app.calendar_client, "create_personal_event", return_value="cal-1"
        ) as create_event,
        mock.patch.object(app.dedupe_store, "mark_processed") as mark_processed,
        mock.patch.object(app.dedupe_store, "mark_skipped") as mark_skipped,
    ):
        app._register_event(_cfg(), object(), "msg-1", event, results, now=NOW)

    # Assert
    create_event.assert_called_once()
    mark_processed.assert_called_once_with("test-table", "msg-1", event, "cal-1")
    mark_skipped.assert_not_called()
    assert results == {"processed": 1, "skipped": 0, "errors": 0}


def test_register_event_offset_start_is_compared_in_its_own_offset():
    # Arrange: 02:00Z is 11:00 JST (past); 04:00Z is 13:00 JST (future)
    past, future = _event("2026-10-06T02:00:00+00:00"), _event("2026-10-06T04:00:00+00:00")

    # Act
    with (
        mock.patch.object(
            app.calendar_client, "create_personal_event", return_value="cal-1"
        ) as create_event,
        mock.patch.object(app.dedupe_store, "mark_skipped") as mark_skipped,
        mock.patch.object(app.dedupe_store, "mark_processed"),
    ):
        app._register_event(_cfg(), object(), "past", past, _results(), now=NOW)
        app._register_event(_cfg(), object(), "future", future, _results(), now=NOW)

    # Assert
    mark_skipped.assert_called_once_with("test-table", "past", past, reason="past_event")
    create_event.assert_called_once()
    assert create_event.call_args.args[2] is future


def test_register_event_past_start_in_dry_run_is_skipped_not_counted_as_processed():
    # Arrange
    results = _results()
    event = _event("2026-10-05T10:00:00+09:00")

    # Act
    with mock.patch.object(app.dedupe_store, "mark_skipped") as mark_skipped:
        app._register_event(_cfg(dry_run=True), object(), "msg-1", event, results, now=NOW)

    # Assert
    mark_skipped.assert_called_once_with("test-table", "msg-1", event, reason="past_event")
    assert results == {"processed": 0, "skipped": 1, "errors": 0}


def test_register_event_low_confidence_past_event_keeps_original_skip_reason():
    # Arrange
    event = ExtractedEvent(
        is_event_confirmation=True,
        confidence=0.1,
        title="Test Meetup",
        start_datetime="2026-10-05T10:00:00+09:00",
        end_datetime=None,
        location=None,
        event_url=None,
    )

    # Act
    with mock.patch.object(app.dedupe_store, "mark_skipped") as mark_skipped:
        app._register_event(_cfg(), object(), "msg-1", event, _results(), now=NOW)

    # Assert
    mark_skipped.assert_called_once_with(
        "test-table", "msg-1", event, reason="low_confidence_or_not_event"
    )


PAST_START = "2020-01-01T10:00:00+09:00"


def _run_handler(cfg: config.Config, *, ics_events=None, gmail_ids=None, extracted=None):
    """Runs lambda_handler with every external boundary stubbed out."""
    with (
        mock.patch.object(app.config, "load", return_value=cfg),
        mock.patch.object(app.google_secrets, "load_google_credentials", return_value=object()),
        mock.patch.object(app.connpass_ics, "load_ics_url", return_value="https://example.com/a.ics"),
        mock.patch.object(app.connpass_ics, "fetch_events", return_value=ics_events or []),
        mock.patch.object(app.gmail_client, "build_service", return_value=object()),
        mock.patch.object(app.gmail_client, "build_query", return_value="q"),
        mock.patch.object(app.gmail_client, "search", return_value=gmail_ids or []),
        mock.patch.object(
            app.gmail_client, "fetch_and_decode", return_value=("body", "example.com")
        ),
        mock.patch.object(app.bedrock_extractor, "extract", return_value=extracted),
        mock.patch.object(app.dedupe_store, "is_processed", return_value=False),
        mock.patch.object(app.dedupe_store, "mark_skipped") as mark_skipped,
        mock.patch.object(app.dedupe_store, "mark_processed"),
        mock.patch.object(app.dedupe_store, "mark_error"),
        mock.patch.object(app.calendar_client, "create_personal_event") as create_event,
    ):
        results = app.lambda_handler({}, None)
    return results, create_event, mark_skipped


def test_lambda_handler_past_connpass_ics_event_is_skipped_without_invite():
    # Arrange
    event = _event(PAST_START)
    cfg = _cfg(connpass_ics_secret_arn="ics-secret")

    # Act
    results, create_event, mark_skipped = _run_handler(cfg, ics_events=[("uid-1", event)])

    # Assert
    create_event.assert_not_called()
    mark_skipped.assert_called_once_with(
        "test-table", "connpass-ics:uid-1", event, reason="past_event"
    )
    assert results == {"processed": 0, "skipped": 1, "errors": 0}


def test_lambda_handler_past_gmail_event_is_skipped_without_invite():
    # Arrange
    event = _event(PAST_START)

    # Act
    results, create_event, mark_skipped = _run_handler(
        _cfg(), gmail_ids=["gmail-1"], extracted=event
    )

    # Assert
    create_event.assert_not_called()
    mark_skipped.assert_called_once_with("test-table", "gmail-1", event, reason="past_event")
    assert results == {"processed": 0, "skipped": 1, "errors": 0}


def test_register_event_past_start_logs_which_event_was_skipped(caplog):
    # Arrange
    caplog.set_level("INFO")

    # Act
    with mock.patch.object(app.dedupe_store, "mark_skipped"):
        app._register_event(
            _cfg(), object(), "msg-1", _event("2026-10-05T10:00:00+09:00"), _results(), now=NOW
        )

    # Assert
    messages = [record.getMessage() for record in caplog.records]
    assert any("msg-1" in m and "2026-10-05T10:00:00+09:00" in m for m in messages)


def test_register_event_future_start_in_dry_run_is_counted_without_calling_calendar():
    # Arrange
    results = _results()

    # Act
    with mock.patch.object(app.calendar_client, "create_personal_event") as create_event:
        app._register_event(
            _cfg(dry_run=True),
            object(),
            "msg-1",
            _event("2026-10-07T10:00:00+09:00"),
            results,
            now=NOW,
        )

    # Assert
    create_event.assert_not_called()
    assert results == {"processed": 1, "skipped": 0, "errors": 0}


def _event_with_end(start: str, end: str | None) -> ExtractedEvent:
    return ExtractedEvent(
        is_event_confirmation=True,
        confidence=0.9,
        title="Test Meetup",
        start_datetime=start,
        end_datetime=end,
        location="Tokyo",
        event_url="https://example.com/events/1",
    )


def test_register_event_started_but_unfinished_event_is_registered():
    # Arrange: started at 10:00, ends at 18:00; NOW is 12:00 JST
    event = _event_with_end("2026-10-06T10:00:00+09:00", "2026-10-06T18:00:00+09:00")
    results = _results()

    # Act
    with (
        mock.patch.object(
            app.calendar_client, "create_personal_event", return_value="cal-1"
        ) as create_event,
        mock.patch.object(app.dedupe_store, "mark_processed"),
        mock.patch.object(app.dedupe_store, "mark_skipped"),
    ):
        app._register_event(_cfg(), object(), "msg-1", event, results, now=NOW)

    # Assert
    create_event.assert_called_once()
    assert results == {"processed": 1, "skipped": 0, "errors": 0}


def test_register_event_date_only_start_today_is_registered_until_the_day_ends():
    # Arrange: an all-day event dated today (10-06); NOW is 12:00 JST on 10-06
    results = _results()

    # Act
    with (
        mock.patch.object(
            app.calendar_client, "create_personal_event", return_value="cal-1"
        ) as create_event,
        mock.patch.object(app.dedupe_store, "mark_processed"),
        mock.patch.object(app.dedupe_store, "mark_skipped"),
    ):
        app._register_event(
            _cfg(), object(), "msg-1", _event_with_end("2026-10-06", None), results, now=NOW
        )

    # Assert
    create_event.assert_called_once()
    assert results == {"processed": 1, "skipped": 0, "errors": 0}


def _skipped_reason_for(event: ExtractedEvent) -> list:
    with (
        mock.patch.object(app.calendar_client, "create_personal_event") as create_event,
        mock.patch.object(app.dedupe_store, "mark_skipped") as mark_skipped,
        mock.patch.object(app.dedupe_store, "mark_processed"),
    ):
        app._register_event(_cfg(), object(), "msg-1", event, _results(), now=NOW)
    create_event.assert_not_called()
    return mark_skipped.call_args_list


def test_register_event_finished_event_is_skipped_as_past():
    # Arrange: both start and end are before NOW (12:00 JST)
    event = _event_with_end("2026-10-06T09:00:00+09:00", "2026-10-06T11:00:00+09:00")

    # Act / Assert
    calls = _skipped_reason_for(event)
    assert calls == [mock.call("test-table", "msg-1", event, reason="past_event")]


def test_register_event_date_only_start_yesterday_is_skipped_as_past():
    # Arrange
    event = _event_with_end("2026-10-05", None)

    # Act / Assert
    calls = _skipped_reason_for(event)
    assert calls == [mock.call("test-table", "msg-1", event, reason="past_event")]


def test_register_event_unreadable_end_falls_back_to_the_start():
    # Arrange: the start is past and the end is garbage, so the start decides
    event = _event_with_end("2026-10-05T10:00:00+09:00", "not-a-datetime")

    # Act / Assert
    calls = _skipped_reason_for(event)
    assert calls == [mock.call("test-table", "msg-1", event, reason="past_event")]


def test_register_event_end_before_start_falls_back_to_the_start():
    # Arrange: inconsistent data (end earlier than start); the start is past
    event = _event_with_end("2026-10-05T10:00:00+09:00", "2026-10-04T10:00:00+09:00")

    # Act / Assert
    calls = _skipped_reason_for(event)
    assert calls == [mock.call("test-table", "msg-1", event, reason="past_event")]
