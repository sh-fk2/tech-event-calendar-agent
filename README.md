# Tech Event Calendar Auto-Registration Agent (tech-event-calendar-agent)

A serverless app that automatically detects "reservation confirmation" emails you receive after registering for tech events (meetups, conferences), extracts the event details with Amazon Bedrock, and registers them on your personal Google Calendar with full details. If you also want to share the event with a company account (Outlook or anything other than Google Calendar), add that email address as a second attendee — Google Calendar will send it a standard meeting-invite email.

The reservation itself is not automated, since it requires a manual login to each site. This app only automates the calendar registration that happens after the confirmation email arrives.

## Components

- **Amazon EventBridge Scheduler** — triggers the Lambda every 3 hours
- **AWS Lambda** (Python 3.12, `src/app.py`) — Gmail search → Bedrock extraction → DynamoDB dedupe check → calendar registration
- **Amazon Bedrock (Nova)** — extracts event name/date-time/location/URL from the email body as structured data (Converse API + tool-use)
- **Amazon DynamoDB** — records processed Gmail message IDs to prevent duplicate registration
- **AWS Secrets Manager** — stores the personal Google account's OAuth refresh token

## Prerequisites

1. An AWS account, [AWS SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html), and Docker (for `sam local invoke`)
2. [Enable access in the console](https://docs.aws.amazon.com/bedrock/latest/userguide/model-access.html) to the target Amazon Bedrock model (default: `amazon.nova-lite-v1:0`) in your deployment region
3. In Google Cloud Console, set up a project for your personal account, enable the Gmail API and Calendar API, and create an OAuth client (Desktop app) to download its JSON
   - **Set the OAuth consent screen to "In production"** (leaving it in "Testing" causes the refresh token to expire after 7 days, silently breaking the polling)
4. (Optional) If you also want to send an invite to a company address, prepare an email address that can receive it (e.g. an Outlook address). Pass it as the `AttendeeEmail` parameter at deploy time.

## Deployment

```bash
sam build
sam deploy --guided
```

If you specify a company email address in the `AttendeeEmail` parameter, that address is added as an attendee to each registered event, and Google Calendar sends it a standard meeting-invite email (with the full title, location, and URL included as-is). Leave it empty to skip sending invites.

After deployment, the secret created in Secrets Manager (`tech-event-agent/google-personal`) still holds placeholder values. Run the following script once to write the real refresh token:

```bash
pip install -r scripts/requirements.txt

python scripts/generate_refresh_token.py \
  --client-secrets-file ~/Downloads/client_secret_personal.json \
  --secret-name tech-event-agent/google-personal
```

A browser window will open — log in and consent with the matching Google account.

> An invite email arriving in Outlook is not guaranteed to be added as a calendar event automatically. Depending on the company's Exchange/Outlook settings, the recipient may need to manually "accept" the invite.

## Testing Locally Against Deployed Resources

To safely try the real Gmail search and Bedrock extraction against your deployed AWS resources (calendar writes are skipped): `DDB_TABLE_NAME` and `SECRET_NAME_PERSONAL_GOOGLE` are required (use the table name / secret name shown in `sam deploy`'s Outputs), and since `GMAIL_SENDER_ALLOWLIST`/`GMAIL_KEYWORDS` default to empty and error out if both are unset, specify at least one:

```bash
DRY_RUN=true \
DDB_TABLE_NAME=<table name from sam deploy Outputs> \
SECRET_NAME_PERSONAL_GOOGLE=tech-event-agent/google-personal \
GMAIL_SENDER_ALLOWLIST="connpass.com,peatix.com,techplay.jp,doorkeeper.jp,eventbrite.com" \
GMAIL_KEYWORDS="予約が完了しました,登録が完了しました,confirmation,registration confirmed" \
python src/app.py
```

Once you're satisfied, set `DRY_RUN=false` to confirm the calendar is actually being registered. To invoke the deployed Lambda directly:

```bash
aws lambda invoke --function-name <PollerFunction Output value> --region <deployment region> /tmp/out.json && cat /tmp/out.json
```

### Classification criteria for reservation-confirmation emails

Bedrock is instructed to strictly judge only whether an email explicitly confirms that the recipient personally just completed a reservation/registration (see `_system_prompt` in `src/bedrock_extractor.py`). The following are treated as `is_event_confirmation=false` even when they include event details such as date/time and location:

- New-event publication notices ("X published an event")
- Reminders about an upcoming event or a closing registration window ("is tomorrow", "registration closes tomorrow")
- Broadcast messages from event organizers or group admins ("message from the event organizer")
- Newsletters or recommended-events digests

The default values of `GMAIL_KEYWORDS` (e.g. "予約が完了しました") don't exactly match connpass's actual wording (e.g. "参加申し込みが完了しました", "参加が確定しました"). This causes no harm in practice since domains in `GMAIL_SENDER_ALLOWLIST` are matched regardless of content, but if you want to pick up confirmation emails from platforms outside the allowlist, adjust the keywords to match their actual wording.

## Environment Variables

These can be overridden as `template.yaml` parameters. The main ones:

| Variable | Default | Description |
|---|---|---|
| `GMAIL_SENDER_ALLOWLIST` | connpass.com, peatix.com, techplay.jp, doorkeeper.jp, eventbrite.com | Sender-domain allowlist |
| `GMAIL_KEYWORDS` | 予約が完了しました, 登録が完了しました, confirmation, registration confirmed | Subject/body keywords |
| `GMAIL_SEARCH_WINDOW` | `newer_than:3d` | Gmail search time window |
| `CONFIDENCE_THRESHOLD` | `0.6` | Skip if Bedrock's confidence is below this value |
| `DEFAULT_DURATION_MINUTES` | `120` | Default duration used when the end time can't be extracted |
| `PERSONAL_CALENDAR_ID` | `primary` | Target calendar ID |
| `ATTENDEE_EMAIL` | (empty) | If set, this address is invited as an attendee via email |
| `DRY_RUN` | `false` | Set to `true` to skip calendar writes |

## Known Limitations (out of scope for v1)

- Updating/deleting the calendar event when a cancellation email is detected is not supported (planned for a future version)
- SES notifications are disabled by default (`SES_ENABLED` is not implemented; may be added if needed)
- Invites sent to `ATTENDEE_EMAIL` include the full content (title, location, URL) as-is — there is no way to hide details from the company side
- Automatic acceptance of the invite on the Outlook side is not guaranteed (manual acceptance may be required)
