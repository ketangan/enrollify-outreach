# Gmail API Reference

Pontora Outreach uses the Gmail API for:

- creating outreach drafts that Ketan manually reviews and sends
- sending the internal daily draft-summary notification to Ketan
- reading Gmail Drafts for duplicate-draft audits
- reading Gmail Sent for send/follow-up detection
- reading Gmail Inbox for replies and bounces

It does not request `gmail.send`. Important caveat: Google requires
`gmail.compose` for draft creation, and that scope can technically send mail
through Gmail's API. The application-level guard is that
`src.gmail_client.send_message()` is disabled, outreach workflows only create
drafts for manual review, and the one internal notification sender is limited
to `SUMMARY_EMAIL_TO`.

## Required Google Cloud Setup

Use the existing Google Cloud project that already owns the Sheets/Places credentials.

1. Google Cloud Console -> project selector -> existing outreach project.
2. APIs & Services -> Library -> enable **Gmail API**.
3. Google Auth Platform -> Branding:
   - App name: `Pontora Outreach`
   - Support email: your personal/support email
   - Contact email: `ketan@mypontora.com`
4. Google Auth Platform -> Data Access:
   - `https://www.googleapis.com/auth/gmail.compose`
   - `https://www.googleapis.com/auth/gmail.readonly`
5. Google Auth Platform -> Audience -> Test users:
   - Add `ketan@mypontora.com`
   - Do not authorize the final token with a personal Gmail account.
6. Google Auth Platform -> Clients -> Create client:
   - Application type: Desktop app
   - Name: `pontora-outreach-local`
7. Download the client JSON to:

```text
config/gmail-oauth-client.json
```

## Local Token Setup

Run:

```bash
python scripts/setup_gmail_oauth.py
```

The script prints a Google authorization URL. Open it while the script keeps
running, then authorize:

```text
ketan@mypontora.com
```

If Google first shows a personal Gmail account, choose **Use another account**
and sign in with `ketan@mypontora.com`.

The script refuses to save a token for the wrong mailbox.

Active Gmail scripts also verify the token mailbox at runtime. If the token was
rotated manually or a Render/GitHub secret points at the wrong account, mailbox
work fails closed instead of creating or auditing drafts under the wrong Gmail
account.

Expected output file:

```text
config/gmail-token.json
```

Never commit either JSON file.

## GitHub Actions Secrets

Base64 encode local JSON files:

```bash
base64 -i config/gmail-oauth-client.json
base64 -i config/gmail-token.json
```

Add these repository secrets:

```text
GMAIL_OAUTH_CLIENT_JSON_B64
GMAIL_TOKEN_JSON_B64
```

The workflow decodes them into `config/` before running the daily pipeline.

## Render Secret Files

Create Render secret files:

```text
/etc/secrets/gmail-oauth-client.json
/etc/secrets/gmail-token.json
```

Set env vars:

```text
GMAIL_OAUTH_CLIENT_PATH=/etc/secrets/gmail-oauth-client.json
GMAIL_TOKEN_PATH=/etc/secrets/gmail-token.json
OUTREACH_EMAIL=ketan@mypontora.com
SUMMARY_EMAIL_TO=kg.ketan@gmail.com
```

## OAuth Testing Caveat

If the OAuth app remains in Testing mode with Gmail scopes, refresh tokens may expire. If that starts breaking the cron, move the OAuth app to a stable Workspace/internal setup before resuming scale.
