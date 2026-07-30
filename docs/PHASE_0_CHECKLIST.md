# Phase 0 Checklist

Use this only when bootstrapping a fresh Pontora Outreach environment.

## Repo

- [ ] Clone the repo.
- [ ] Create and activate a Python virtualenv.
- [ ] Install dependencies:

```bash
pip install -r requirements.txt
```

## Google Cloud

- [ ] Select the existing outreach Google Cloud project.
- [ ] Enable Places API (New).
- [ ] Enable Sheets API and Drive API.
- [ ] Enable Gmail API.
- [ ] Create/reuse the Places API key.
- [ ] Create/reuse the Sheets service account JSON at:

```text
config/google-service-account.json
```

- [ ] Create Gmail OAuth Desktop client JSON at:

```text
config/gmail-oauth-client.json
```

- [ ] Google Auth Platform -> Audience -> Test users includes:

```text
ketan@mypontora.com
```

- [ ] Run Gmail OAuth setup:

```bash
python scripts/setup_gmail_oauth.py
```

- [ ] Confirm it saved:

```text
config/gmail-token.json
```

## Google Sheet

- [ ] Sheet is named `Pontora Outreach`.
- [ ] Tabs exist: `Leads`, `Already_Contacted`, `Coverage`, `Templates`, `No_Website_Schools`, `Archive`, `Click_Log`.
- [ ] Sheet is shared with the service account email as Editor.
- [ ] Templates use Pontora/mypontora.com only.

## Environment

- [ ] Copy `.env.example` to `.env`.
- [ ] Fill in:

```text
ANTHROPIC_API_KEY
GOOGLE_PLACES_API_KEY
GOOGLE_SHEETS_CREDENTIALS_PATH
GOOGLE_SHEET_ID
BRAND_NAME
PRODUCT_DOMAIN
PRODUCT_URL
DEMO_URL
OUTREACH_EMAIL
SUMMARY_EMAIL_TO
OUTREACH_ADMIN_URL
GMAIL_OAUTH_CLIENT_PATH
GMAIL_TOKEN_PATH
HOME_ZIP
```

## Verification

- [ ] `python scripts/run_phase_1_discovery.py --list-regions`
- [ ] `python scripts/check_template_brand.py`
- [ ] `python scripts/audit_drafts.py`
- [ ] `python scripts/sent_email_count.py`
- [ ] Create one test Gmail draft with a safe test lead before resuming production outreach.
