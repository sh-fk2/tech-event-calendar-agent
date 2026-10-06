# Changelog

## v3

- **Skip events that have already finished**: before calling the Calendar API, the poller now skips an event whose end (or its start, when no readable end exists) is already past, and records it as `SKIPPED_PAST_EVENT`. This stops a recovery run after an outage from emailing invitations for events that are over. A start without an offset is read as Asia/Tokyo; a date-only start covers the whole day; an unreadable start is recorded as an error instead of aborting the run.
- **Re-authorization endpoint (`ReauthFunction`)** and a CloudWatch alarm that notifies when the poller fails (for example when the Google refresh token expires).

## v2

- **connpass ICS feed integration**: connpass events can now be registered directly from connpass's own personal "joined events" iCalendar subscription feed, instead of relying on Gmail search + Bedrock classification for that platform. Since connpass itself only includes events the user actually joined, this eliminates the false-positive risk (event-published notices, reminders, organizer broadcasts) that the Gmail+Bedrock path is exposed to for connpass. Configure via the `ConnpassIcsSecret` Secrets Manager secret; see the README's "Optional: connpass ICS feed" section for setup and migration steps. `GmailSenderAllowlist` no longer includes `connpass.com` by default for new deployments.
- **Multiple attendees**: `AttendeeEmail` (single address) was replaced with `AttendeeEmails` (comma-separated, multiple addresses).
- **Event visibility control**: new `EventVisibility` parameter (`default` / `private`). `private` hides the event's details from other people who have viewing access to the calendar (e.g. coworkers with shared-calendar visibility), while invited attendees still see full details.

## v1

- Initial release: Gmail search + Amazon Bedrock (Nova) classification detects genuine reservation-confirmation emails from connpass, Peatix, TECH PLAY, Doorkeeper, and Eventbrite, and registers them on a personal Google Calendar. Optional single-attendee invite via `AttendeeEmail`.
