# Pontora Outreach — Project Source of Truth

**Last updated:** 2026-07-26
**Status:** MVP shipped. Outreach app rebrand/Gmail migration is implemented; obsolete Zoho modules are retained until one production Gmail draft/sync cycle is verified.
**Repo:** private GitHub repo currently still named `enrollify-outreach`

## Who & What

- **Owner:** Ketan Gandhi, co-founder of Pontora (`mypontora.com`)
- **Co-founder:** Natasha Julka (on the website, but not named in cold outreach)
- **Goal:** automate cold outreach to small activity-based schools without online enrollment systems
- **Target volume:** about 20-25 emails/day when outreach resumes
- **Geographic start:** LA area (90045) -> California -> US

## Current Rebrand State

- Product/customer-facing brand: **Pontora**
- Product domain: `mypontora.com`
- Outreach mailbox: `ketan@mypontora.com`
- Demo URL: `https://mypontora.com/demo`
- Outreach admin target URL: `https://enrollify-admin.onrender.com`
  - Temporary Option A decision: keep the existing Render hostname until a slow period. The Render service display name is Pontora, but the original `onrender.com` subdomain still contains the old name.
- Old outbound queue is drained. Future first-touch and follow-up emails should be sent from Pontora only.
- Live Google Sheet templates must stay on Pontora wording. `scripts/check_template_brand.py` should pass before outreach resumes.

## Architecture Decisions

| Area | Decision |
|---|---|
| Data store | Google Sheets |
| LLM | Claude Haiku via Anthropic API |
| Discovery | Google Places API (New) |
| Mailbox | Pontora Google Workspace / Gmail |
| Mail API | Gmail API with `gmail.compose` + `gmail.readonly`; active code is draft-only |
| Webapp | FastAPI + Jinja2 + HTMX on Render free tier |
| Runtime | Python 3.14 local, Python 3.12 on Render/GitHub Actions |
| Secrets | `.env` locally, GitHub repo secrets, Render env vars + secret files |

The app creates Gmail drafts only. Ketan manually reviews and sends from Gmail. Internal summary/reply/bounce emails are not auto-sent. Google requires `gmail.compose` for draft creation, and that scope can technically send mail through Gmail's API, so the actual safety control is in code: active workflows never call Gmail send endpoints and `src.gmail_client.send_message()` fails closed. Runtime Gmail access also checks that the OAuth token belongs to `OUTREACH_EMAIL` before doing mailbox work.

## Target School Categories

Dance studios, music schools, sports academies, preschools, daycares, martial arts, art studios, gymnastics/cheer, swim schools, independent tutoring/learning centers, language schools for kids, coding/STEM for kids, and independent Montessori schools.

Disqualify leads with clear online enrollment/login/payment systems or known third-party enrollment platforms. When uncertain, route to manual classification instead of emailing.

## Google Sheet Schema

Spreadsheet: `Pontora Outreach` once renamed by Ketan. Current sheet id stays the same.

### `Leads`

```text
id | name | website | category | city | state | zip | phone | address |
discovered_date | status | enrollment_method | owner_name | owner_title |
owner_source_url | best_email | email_confidence | last_action |
sent_at | sent_message_id | follow_up_at | follow_up_sent_at | replied_at |
do_not_contact_reason | notes
```

### Status Enum

```text
pending_classify
needs_enrollment_system_classification
online_system_exclude
ready_for_owner_lookup
needs_owner_review
ready_to_send
awaiting_approval
sent
replied
bounced
closed_no_reply
already_contacted
do_not_contact
no_website_collected
```

`awaiting_approval` now means a Gmail draft exists and is waiting for manual send.

### Other Tabs

- `Already_Contacted` — prior outreach.
- `Coverage` — zip progress.
- `Templates` — `template_id | subject | body | observation | last_updated`.
- `No_Website_Schools` — Phase 10 holding bin.
- `Archive` — moved rows from terminal statuses.
- `Click_Log` — demo-link click tracking.

## Pipeline

| Phase | Script(s) | Purpose |
|---|---|---|
| 1 — Discovery | `scripts/run_phase_1_discovery.py` | Google Places -> `Leads` as `pending_classify`; auto-runs internal dedupe. |
| 1.5 — Internal duplicate check | `src/dedupe_within_leads.py` | Demotes only strong internal duplicates. Distinct verified emails can remain eligible. |
| 2 — Contact duplicate check | `scripts/run_phase_2_dedupe.py` | Checks Already_Contacted + Archive using strong evidence. |
| 3 — Classify | `scripts/run_phase_3_classify.py` | URL prefilter -> keyword scan -> Haiku classification. |
| 4 — Owner lookup | `scripts/run_phase_4_owners.py` | Fetch common pages -> extract emails -> Haiku owner/contact selection -> web-search fallback. |
| 5 — Draft | `scripts/run_phase_5_drafts.py` | Audit preflight -> render template -> create Gmail draft -> mark `awaiting_approval`. |
| 6 — Sync | `scripts/run_phase_6_sync.py` | Gmail Sent marks `sent`; Gmail Inbox marks replies/bounces. |
| 6b — Follow-up draft | `scripts/run_phase_6_followup.py` | Audit-gated threaded Gmail follow-up drafts after 7 days. |
| Lifecycle | `scripts/run_close_stale.py` | Sent + no reply after follow-up window -> `closed_no_reply`. |
| Cleanup | `scripts/run_cleanup.py` | Move terminal statuses to Archive. |
| Daily orchestrator | `scripts/run_daily.py` | sync -> follow-up drafts -> owners -> initial drafts. |
| Admin one-offs | `scripts/audit_drafts.py`, `scripts/list_drafts_with_leads.py`, `scripts/sent_email_count.py` | Gmail-based audit/inspection/counting. |

## Webapp

Routes:

- `/` — pipeline dashboard, recommendations, common tasks.
- `/coverage` — region progress.
- `/leads` — filterable lead list.
- `/review` — classify / owner / pre-send review.
- `/jobs`, `/jobs/{id}` — subprocess job status.

Critical TODO: add authentication. Until auth exists, do not share the admin URL publicly.

## Secrets

```text
ANTHROPIC_API_KEY=
GOOGLE_PLACES_API_KEY=
GOOGLE_SHEETS_CREDENTIALS_PATH=./config/google-service-account.json
GOOGLE_SHEET_ID=
BRAND_NAME=Pontora
PRODUCT_DOMAIN=mypontora.com
PRODUCT_URL=https://mypontora.com
DEMO_URL=https://mypontora.com/demo
OUTREACH_EMAIL=ketan@mypontora.com
OUTREACH_ADMIN_URL=https://enrollify-admin.onrender.com
GMAIL_OAUTH_CLIENT_PATH=./config/gmail-oauth-client.json
GMAIL_TOKEN_PATH=./config/gmail-token.json
DEFAULT_DAILY_EMAIL_CAP=20
WORKING_HOURS_START=9
WORKING_HOURS_END=17
TIMEZONE=America/Los_Angeles
HOME_ZIP=90045
```

GitHub Actions also needs:

```text
GOOGLE_SERVICE_ACCOUNT_JSON_B64
GMAIL_OAUTH_CLIENT_JSON_B64
GMAIL_TOKEN_JSON_B64
```

Render should use secret files:

```text
/etc/secrets/google-service-account.json
/etc/secrets/gmail-oauth-client.json
/etc/secrets/gmail-token.json
```

## Gmail OAuth Setup

1. Enable Gmail API in the existing Google Cloud project.
2. Create OAuth branding/app as Pontora Outreach.
3. Add `ketan@mypontora.com` under Google Auth Platform -> Audience -> Test users.
4. Create a Desktop OAuth client and download it to `config/gmail-oauth-client.json`.
5. Run `python scripts/setup_gmail_oauth.py`.
6. Authorize exactly `ketan@mypontora.com`.
7. Confirm `config/gmail-token.json` exists.

If the OAuth app is in testing mode, Gmail refresh tokens may expire. If that becomes operationally painful, move to a more stable Workspace/internal OAuth setup.

## Email Templates

Templates live in the Google Sheet `Templates` tab. They should use Pontora wording and can use these placeholders:

```text
{{owner_first_name}}
{{school_name}}
{{category}}
{{specific_observation}}
{{lead_id}}
{{feature_bullets}}
{{brand_name}}
{{product_domain}}
{{product_url}}
{{demo_url}}
{{website_mock_addendum}}
```

Before outreach resumes, run a read-only template check and ensure no old brand/domain remains.

## Website Mock Addendum

Optional website-refresh mocks are manual-gated. Mark a lead as a mock candidate from Review or In Progress, then run:

```bash
python scripts/setup_website_mock_sheet.py
python scripts/generate_website_mocks.py --base-url https://mocks.mypontora.com --write-sheet
```

The generator creates two versions by default for each mock type (`preschool`, `music`, `sports`) and stores all public URLs in `website_mock_payload` as JSON. Follow-up drafts append the `website_mock_followup_addendum` template only when `website_mock_status=generated` and at least one mock URL exists.

Do not deploy these files into the manually managed main `mypontora.com` website project unless the full marketing site is also in the same deploy directory. Use a separate Cloudflare Workers static-assets app/subdomain such as `mocks.mypontora.com` for this workflow.

Mock generation/deployment is non-blocking in GitHub Actions. If Cloudflare fails, the daily outreach pipeline still runs, and Sheet mock URLs are not written.

## Operational Notes

- Daily run creates Gmail drafts only; Ketan sends manually.
- Daily run stops before drafting if Gmail sync fails; sending against stale reply/bounce data is not acceptable.
- Follow-up drafting requires the original sent message to exist in the Pontora Gmail mailbox. Legacy pre-rebrand sends are skipped and marked with `phase6_followup_skipped_missing_gmail_original`.
- Click tracking still writes to `Click_Log`; `{{lead_id}}` renders into follow-up demo URLs.
- Website mock pages include the same human-gesture-filtered click logger, so mock page activity appears in `Click_Log` when links use `utm_content=<lead_id>`.
- Run `python scripts/show_clicks.py --since-days 7` on Mondays to find clickers.
- Run stale-close and cleanup weekly:

```bash
python scripts/run_close_stale.py
python scripts/run_cleanup.py
python scripts/run_close_stale.py --commit
python scripts/run_cleanup.py --commit
```

## Legacy Cleanup

The old Zoho modules are intentionally retained only until Gmail draft creation and Gmail sync are verified end-to-end:

- `src/zoho.py`
- `src/zoho_sync.py`

After validation, remove these files and the legacy Zoho env variables from `src/config.py`.
