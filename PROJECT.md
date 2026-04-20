# Enrollify Outreach — Project Source of Truth

> **⚠️ READ THIS FIRST if you're Claude in a new session.**
> This document IS the memory. At the start of every session, Ketan pastes this entire file as his first message. Before closing any session, output an updated version of this file in a code block for Ketan to commit to the repo. Every decision, schema change, phase transition, or tool swap must be reflected here. If something is ambiguous, ask — don't guess.

**Last updated:** 2026-04-16
**Current phase:** 0 — Setup (in progress)
**Repo:** (to be created by Ketan — GitHub username: see placeholder below)

---

## Who & what

- **Owner:** Ketan Gandhi, co-founder of Enrollify (enrollifyapp.com)
- **Co-founder:** Natasha Julka
- **Goal of this project:** Automate cold outreach to small activity-based schools (dance studios, preschools, sports academies, etc.) that lack online enrollment systems. Currently done manually — schools sourced via Google Maps, owner contact found via website/LinkedIn, personalized email sent, logged in a Google Sheet.
- **Target daily volume:** ~20–25 emails (cap set by Zoho free tier + approval throughput)
- **Budget cap:** $20/month ideal, $100/month absolute max
- **Geographic start:** LA area (zip 90045 and concentric expansion), then California, then US

## Working agreement with Claude

- **Claude has no memory between sessions.** This file IS the memory.
- **Session protocol:**
  1. New chat → Ketan pastes this file as first message + states what to work on
  2. Claude confirms sync, proceeds
  3. Before closing session → Claude outputs updated `PROJECT.md` in a code block
  4. Ketan commits to repo
- **Tone:** direct, no flattery, push back when Ketan is wrong. Ketan is a senior engineer — skip the hand-holding on code.

---

## Architecture decisions (locked in)

| Area | Decision | Why |
|---|---|---|
| Data store | Google Sheets | Visibility, mobile-accessible, manual edit friendly |
| LLM | Claude Haiku via Anthropic API | Cheapest capable model; Ketan's Pro subscription doesn't cover API |
| Scraping | Google Places API | Free within $200/mo credit, structured data, no ToS risk |
| Email sending | Zoho Mail free tier | Free custom domain email, proper SPF/DKIM, 25 sends/day (matches our cap) |
| Email access protocol | IMAP (for drafts & reply detection) | Zoho free tier supports IMAP if manually enabled |
| Push notifications | Pushover free tier | One-time $5 app purchase, free API, reliable on iOS/Android |
| Runtime | Python 3.11+ on Ketan's Mac | Manual trigger initially; Phase 9 may add GitHub Actions cron |
| Secrets | `.env` file, git-ignored | Never commit real keys |

## Tools NOT chosen and why

- **Clay.com** — all-in-one solution, Ketan rejected due to cost/complexity preference
- **Hunter.io** — free tier only 25 searches/month, not workable
- **Apollo.io** — strong but paid; revisit at scale
- **Apify** — free tier exists but Google Places API is cleaner for this use case
- **Google Workspace** — $7/mo, Ketan prefers free tier (Zoho)
- **Cloudflare Email** — only forwards inbound, can't send; insufficient alone
- **Second outreach domain** — deferred until Enrollify has 5+ paying customers

---

## Target school categories

Active list (all processed in parallel):

- Dance studios
- Music schools
- Sports academies
- Pre-schools
- Day-cares
- Martial arts schools
- Art studios
- Gymnastics / cheer academies
- Swim schools
- Tutoring / learning centers (independents, not chains)
- Language schools for children
- Coding / STEM academies for children
- Montessori schools (independents)

## Disqualification criteria (mark as `online_system_exclude`)

Any of:
- Has an online enrollment form on their site
- Has a parent/student "Login" button/portal
- Uses recognized third-party enrollment software (ClassDojo, Jackrabbit, DanceStudio-Pro, Brightwheel, Procare, Mindbody, etc.)

When uncertain: mark as `needs_manual_review`, never auto-email.

---

## Phase plan

| Phase | Status | Goal |
|---|---|---|
| 0 | IN PROGRESS | Setup: accounts, keys, sheet, Zoho, repo scaffolding |
| 1 | Not started | Lead discovery via Google Places API (no LLM) |
| 2 | Not started | Dedupe against existing contacted list |
| 3 | Not started | Enrollment method classification (Claude Haiku) |
| 4 | Not started | Owner discovery + email guessing + SMTP verify |
| 5 | Not started | Draft generation + morning approval email |
| 6 | Not started | Follow-up scheduling + reply detection (Pushover alerts) |
| 7 | Not started | Coverage tracking + zip code expansion logic |
| 8 | Not started | Mobile-friendly approval UI |
| 9 | Not started | GitHub Actions cron for unattended runs |

**MVP = Phases 0–6.** 7–9 are improvements, decided after MVP proves out.

---

## Repo structure (target)

```
enrollify-outreach/
├── PROJECT.md                    # This file — source of truth
├── README.md                     # Brief usage notes
├── .env.example                  # Template for secrets (commit this)
├── .env                          # Real secrets (git-ignored)
├── .gitignore
├── requirements.txt              # Python deps
│
├── src/
│   ├── __init__.py
│   ├── config.py                 # Load .env, constants
│   ├── sheets.py                 # Google Sheets client wrapper
│   ├── places.py                 # Google Places API (Phase 1)
│   ├── dedupe.py                 # Phase 2
│   ├── classify.py               # Enrollment method classifier (Phase 3)
│   ├── owner_finder.py           # Owner name finder (Phase 4)
│   ├── email_guesser.py          # Email construction + verification (Phase 4)
│   ├── drafter.py                # Email draft generation (Phase 5)
│   ├── zoho.py                   # Zoho IMAP + SMTP client (Phase 5-6)
│   ├── follow_up.py              # Phase 6
│   ├── replies.py                # Reply detection (Phase 6)
│   └── notify.py                 # Pushover wrapper (Phase 6)
│
├── config/
│   ├── templates/                # Email templates (markdown)
│   │   ├── contact_form.md
│   │   ├── email.md
│   │   ├── pdf_form.md
│   │   └── follow_up.md
│   └── prompts/                  # Claude prompts (versioned)
│       ├── classify.txt
│       ├── find_owner.txt
│       └── draft_email.txt
│
├── scripts/
│   ├── run_phase_1_discovery.py  # One CLI per phase for manual runs
│   ├── run_phase_2_dedupe.py
│   ├── run_phase_3_classify.py
│   ├── run_phase_4_owners.py
│   ├── run_phase_5_drafts.py
│   ├── run_phase_6_followup.py
│   └── run_daily.py              # Orchestrator — runs full daily pipeline
│
├── data/                          # Local cache / debug output (git-ignored)
│   └── .gitkeep
│
└── docs/
    ├── setup_zoho.md             # Step-by-step Zoho DNS walkthrough
    ├── setup_google.md           # Google Cloud + Sheets setup
    ├── setup_anthropic.md        # API key setup
    └── sheet_schema.md           # Sheet tabs & columns reference
```

---

## Google Sheet schema

**Spreadsheet name:** `Enrollify Outreach`
**URL:** (to be filled after Ketan creates the sheet)
**Share with:** Google service account email (created in Phase 0 setup)

### Tab 1: `Leads` (main working tab)

| Column | Type | Description |
|---|---|---|
| id | string | `{zip}-{sequence}` — e.g. `90045-001` |
| name | string | School name |
| website | URL | Primary website |
| category | enum | dance / music / sports / preschool / daycare / martial_arts / art / gymnastics / swim / tutoring / language / coding_stem / montessori / other |
| city | string | |
| state | string | 2-letter |
| zip | string | 5-digit |
| phone | string | |
| address | string | |
| discovered_date | date | When added to sheet (ISO) |
| status | enum | see status values below |
| enrollment_method | enum | online_system_exclude / email_or_phone_qualify / pdf_form_qualify / contact_form_qualify / needs_manual_review |
| owner_name | string | |
| owner_title | string | e.g. "Owner", "Director" |
| owner_source_url | URL | Where we found them |
| best_email | email | |
| email_confidence | enum | high / medium / low / unverified |
| last_action | string | Short note of last thing done |
| sent_at | datetime | When initial email sent (ISO) |
| follow_up_at | date | When to send follow-up (sent_at + 7d) |
| follow_up_sent_at | datetime | |
| replied_at | datetime | When a reply was detected |
| notes | string | Free-text, human-written |

### Status enum (lifecycle)

```
pending_classify       → discovered, not yet classified
needs_manual_review    → AI couldn't decide, Ketan needs to act
online_system_exclude  → disqualified (has online enrollment)
ready_for_owner_lookup → classified as qualified, need to find owner
ready_for_email_guess  → owner found, need to construct email
ready_to_send          → ready for draft generation
awaiting_approval      → draft created, awaiting morning review
sent                   → initial email sent, waiting for reply/follow-up
follow_up_sent         → follow-up sent
replied                → 🚨 reply received, Ketan engaging
closed_no_reply        → after follow-up, no response — dead lead
already_contacted      → imported from existing sheet, skip
do_not_contact         → Ketan manually marked — never email
```

### Tab 2: `Already_Contacted`

Imported from Ketan's existing Google Sheet. Columns: `school_name`, `email`, `contacted_date`, `outcome`, `notes`. Used by Phase 2 dedupe only.

### Tab 3: `Coverage`

| Column | Description |
|---|---|
| zip | |
| city | |
| total_found | Schools discovered in this zip |
| qualified | Passed classification |
| contacted | Sent email |
| replied | Got a reply |
| status | not_started / in_progress / complete |
| started_date | |
| completed_date | |

### Tab 4: `Templates`

| Column | Description |
|---|---|
| template_id | contact_form / email / pdf_form / follow_up |
| subject | With `{{placeholders}}` |
| body | With `{{placeholders}}` |
| last_updated | |

Placeholders supported: `{{owner_first_name}}`, `{{school_name}}`, `{{category}}`, `{{specific_observation}}`.

---

## Secrets (`.env` variables)

```
# Anthropic API
ANTHROPIC_API_KEY=

# Google Cloud (Places API + Sheets API)
GOOGLE_PLACES_API_KEY=
GOOGLE_SHEETS_CREDENTIALS_PATH=./config/google-service-account.json
GOOGLE_SHEET_ID=

# Zoho Mail
ZOHO_EMAIL=ketan@enrollifyapp.com
ZOHO_APP_PASSWORD=            # NOT your login password — app-specific password
ZOHO_IMAP_HOST=imap.zoho.com
ZOHO_IMAP_PORT=993
ZOHO_SMTP_HOST=smtp.zoho.com
ZOHO_SMTP_PORT=465

# Pushover (Phase 6)
PUSHOVER_USER_KEY=
PUSHOVER_APP_TOKEN=

# Project config
DEFAULT_DAILY_EMAIL_CAP=20
WORKING_HOURS_START=9          # 9am PT
WORKING_HOURS_END=17           # 5pm PT
TIMEZONE=America/Los_Angeles
HOME_ZIP=90045
```

---

## Progress log

### 2026-04-16 — Session 1
- Agreed on phased approach (0-9)
- Confirmed tool stack (Zoho, Claude Haiku, Google Places, Sheets, Pushover)
- Confirmed Ketan will manually execute approval step for MVP; mobile UI is Phase 8
- Confirmed session protocol: paste PROJECT.md at start, commit updated version at end
- **Phase 0 started**: repo structure defined, setup walkthroughs produced

### Open questions (not blocking)
- Zoho free tier IMAP: confirmed supported but needs manual enable — document in setup guide
- Whether to use Google Sheets API directly or `gspread` library (recommend `gspread`)
- Whether to version control email templates in repo or only in Sheets (recommend both — Sheets for mutation, repo as backup)
