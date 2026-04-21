# Enrollify Outreach — Project Source of Truth

> **⚠️ READ THIS FIRST if you're Claude in a new session.**
> This document IS the memory. At the start of every session, Ketan pastes this entire file as his first message. Before closing any session, output an updated version of this file in a code block for Ketan to commit to the repo. Every decision, schema change, phase transition, or tool swap must be reflected here. If something is ambiguous, ask — don't guess.

**Last updated:** 2026-04-21
**Current phase:** 0 complete — ready for Phase 1
**Repo:** (Ketan's private GitHub repo — `enrollify-outreach`)

---

## Who & what

- **Owner:** Ketan Gandhi, co-founder of Enrollify (enrollifyapp.com)
- **Co-founder:** Natasha Julka (referenced on the website, but NOT named in cold outreach emails — team size is deliberately not disclosed in outreach)
- **Goal of this project:** Automate cold outreach to small activity-based schools (dance studios, preschools, sports academies, etc.) that lack online enrollment systems.
- **Target daily volume:** ~20–25 emails
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
| Email sending | Zoho Mail Lite ($1/user/month) | Upgraded from free tier — free tier doesn't include IMAP; Mail Lite does |
| Email access protocol | IMAP (drafts) + SMTP (send) | For programmatic draft writing and sending |
| Push notifications | Pushover free tier | One-time $5 app purchase, free API, reliable on iOS/Android |
| Runtime | Python 3.11+ on Ketan's Mac | Manual trigger initially; Phase 9 may add GitHub Actions cron |
| Secrets | `.env` file, git-ignored | Never commit real keys |

## Tools NOT chosen and why

- Clay.com, Apollo.io, Hunter.io, Apify, Google Workspace — see earlier rationale
- Second outreach domain — deferred until Enrollify has 5+ paying customers
- AI-generated `specific_observation` — rejected in favor of static per-method paragraphs (simpler, cheaper, zero LLM failure mode for this piece)

---

## Target school categories

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

## Special case: schools without websites

Schools discovered via Google Places that have **no website** are NOT discarded. Their data is still collected including:
- Google reviews (text, rating, date, reviewer)
- Yelp reviews (if available — TBD if scraping allowed within Yelp ToS; may need to skip)
- Basic business info (name, address, phone, hours, category)

These schools are marked with `status = no_website_collected` and set aside from the main outreach flow. Phase 10 (deferred) may use this data for a separate "website + enrollment" pitch. See deferred features below.

---

## Phase plan

| Phase | Status | Goal |
|---|---|---|
| 0 | **DONE** ✅ | Setup: accounts, keys, sheet, Zoho, repo scaffolding, templates written |
| 1 | Ready to start | Lead discovery via Google Places API (no LLM) — including no-website schools |
| 2 | Not started | Dedupe against existing contacted list |
| 3 | Not started | Enrollment method classification (Claude Haiku) |
| 4 | Not started | Owner discovery + email guessing + SMTP verify |
| 5 | Not started | Draft generation + morning approval email |
| 6 | Not started | Follow-up scheduling + reply detection (Pushover alerts) |
| 7 | Not started | Coverage tracking + zip code expansion logic |
| 8 | Not started | Mobile-friendly approval UI |
| 9 | Not started | GitHub Actions cron for unattended runs |
| **10** | **Deferred** | Website-builder upsell pitch to no-website schools |

**MVP = Phases 0–6.** 7–9 are improvements. **10 is deferred** — data is collected during Phase 1 but the outreach feature is not built until Enrollify has 10+ paying customers, per owner's decision to avoid dilution of core product focus.

---

## Google Sheet schema

**Spreadsheet name:** `Enrollify Outreach`
**Shared with:** Google service account email as Editor

### Tab 1: `Leads`

Columns (order matters — code indexes by position):

```
id | name | website | category | city | state | zip | phone | address | discovered_date | status | enrollment_method | owner_name | owner_title | owner_source_url | best_email | email_confidence | last_action | sent_at | follow_up_at | follow_up_sent_at | replied_at | notes
```

### Status enum

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
no_website_collected   → no website; data collected for Phase 10
```

### Enrollment method enum

```
online_system_exclude, email_or_phone_qualify, pdf_form_qualify,
contact_form_qualify, needs_manual_review
```

### Email confidence enum

```
high, medium, low, unverified
```

### Tab 2: `Already_Contacted`

Columns: `school_name | email | contacted_date | outcome | notes`
Imported from Ketan's existing "already emailed" sheet.

### Tab 3: `Coverage`

Columns: `zip | city | total_found | qualified | contacted | replied | status | started_date | completed_date`

Status enum: `not_started, in_progress, complete`

### Tab 4: `Templates`

Columns: `template_id | subject | body | observation | last_updated`

Rows:
- `contact_form` — initial email for schools using contact forms
- `email` — initial email for schools using direct email enrollment
- `pdf_form` — initial email for schools using downloadable PDF forms
- `follow_up` — follow-up email (`observation` column empty)

Placeholders supported in body: `{{owner_first_name}}`, `{{school_name}}`, `{{category}}`, `{{specific_observation}}`.
Placeholders supported in observation: `{{school_name}}`.

### NEW — Tab 5: `No_Website_Schools` (for Phase 10)

Added to collect data on schools discovered without websites. Columns:

```
id | name | category | city | state | zip | phone | address | discovered_date | google_rating | google_review_count | google_reviews_json | yelp_url | yelp_rating | yelp_review_count | yelp_reviews_json | status | notes
```

`google_reviews_json` and `yelp_reviews_json` hold arrays of review objects serialized as JSON strings (keeps things simple in Sheets).

Status enum for this tab: `collected, pitched, responded, closed_won, closed_lost, do_not_contact`

---

## Secrets (`.env` variables)

```
# Anthropic API
ANTHROPIC_API_KEY=

# Google Cloud (Places API + Sheets API)
GOOGLE_PLACES_API_KEY=
GOOGLE_SHEETS_CREDENTIALS_PATH=./config/google-service-account.json
GOOGLE_SHEET_ID=

# Zoho Mail (Mail Lite paid tier)
ZOHO_EMAIL=ketan@enrollifyapp.com
ZOHO_APP_PASSWORD=
ZOHO_IMAP_HOST=imap.zoho.com
ZOHO_IMAP_PORT=993
ZOHO_SMTP_HOST=smtp.zoho.com
ZOHO_SMTP_PORT=465

# Pushover (Phase 6)
PUSHOVER_USER_KEY=
PUSHOVER_APP_TOKEN=

# Project config
DEFAULT_DAILY_EMAIL_CAP=20
WORKING_HOURS_START=9
WORKING_HOURS_END=17
TIMEZONE=America/Los_Angeles
HOME_ZIP=90045
```

---

## Email templates (finalized)

All 3 initial templates share the same body. Only `observation` differs per method.

### Subject (all initial templates)
```
Reimagining enrollment for smaller schools
```

### Subject (follow-up)
```
Re: Reimagining enrollment for smaller schools
```

### Observations (per method, static)

**contact_form:**
`I was on {{school_name}}'s site earlier and noticed that families interested in signing up are asked to fill out a contact form to get started.`

**email:**
`I came across {{school_name}}'s website and saw that prospective families are directed to email the school directly to begin enrollment.`

**pdf_form:**
`I was browsing {{school_name}}'s website and noticed enrollment starts with a downloadable PDF form that families fill out and return.`

### Body (shared across contact_form, email, pdf_form)

```html
Hi {{owner_first_name}},<br><br>

{{specific_observation}}<br><br>

We've been building Enrollify — enrollment software designed specifically for {{category}} schools like yours, where the big platforms are overkill and scheduling tools treat enrollment as an afterthought.<br><br>

Here's what's included:<br>
<ul>
<li>Custom-built enrollment forms tailored to your programs and branding</li>
<li>A clean dashboard where every submission lands organized and searchable</li>
<li>Built-in reporting on enrollment trends and application activity</li>
<li>Lead management so prospective families don't slip through the cracks</li>
<li>AI-generated summaries of each applicant, scored against your criteria</li>
<li>One-click exports to Brightwheel and other tools you may already use</li>
<li>Zero setup on your end — no servers, no databases, no maintenance</li>
</ul><br>

If you'd like to see it in action, I can send over a custom enrollment form built specifically for {{school_name}}, ready to try. No call required, no commitment. If you like it, we'll set you up with an extended free trial on the full platform — everything unlocked.<br><br>

A bit of context: Enrollify is built by a team with decades of experience shipping software at companies large and small, and with direct experience running enrollment for online schools — which is where the idea came from.<br><br>

Happy to send one over if you'd like to see it.<br><br>

Thanks,<br>
Ketan<br>
<a href="https://enrollifyapp.com">enrollifyapp.com</a>
```

### Follow-up body

```html
Hi {{owner_first_name}},<br><br>

Just wanted to follow up in case my note from last week got buried.<br><br>

We've been working on a small demo inspired by {{school_name}}'s website that shows what enrollment could look like with a simple online form and admin view — instead of managing everything over email.<br><br>

If you're open to it, happy to send over a demo link, or walk you through it briefly if that's easier.<br><br>

Thanks,<br>
Ketan<br>
<a href="https://enrollifyapp.com">enrollifyapp.com</a>
```

---

## Progress log

### 2026-04-16 — Session 1
- Agreed on phased approach (0-9)
- Confirmed tool stack
- Confirmed session protocol

### 2026-04-21 — Session 2
- **Phase 0 complete.** Zoho Mail Lite set up (upgraded from free due to IMAP limitation), all DNS records configured, IMAP enabled, app password generated.
- Google Cloud project, Places API key, Sheets API, service account all set up.
- Google Sheet created with 4 tabs, dropdowns, conditional formatting.
- All 4 email templates finalized and in the Sheet.
- Added **deferred Phase 10**: website-builder upsell to no-website schools. Data collected during Phase 1, outreach feature postponed until Enrollify has 10+ paying customers.
- Added **Tab 5: `No_Website_Schools`** to schema.
- Switched `specific_observation` from Claude-generated to static per-method paragraphs (cost + reliability win).
- Decided Natasha is on the website but NOT named in cold outreach (avoid disclosing team size).
- Ready for Phase 1.

### Open questions for Phase 1
- Yelp scraping: is it within Yelp's ToS at our volume? Investigate before writing scraper. If not, skip Yelp for Phase 1, fall back to Google reviews only.
- Confirm Google Places API returns review data via `places.getDetails` — verify field availability in "New" API.

## Progress log

### 2026-04-16 — Session 1
- Agreed on phased approach (0-9)
- Confirmed tool stack
- Confirmed session protocol

### 2026-04-21 — Session 2 (Phase 1 delivered)
- Phase 1 code written:
  - src/config.py, src/sheets.py, src/skip_lists.py
  - src/regions.py + config/regions.yaml (using pgeocode, not uszipcode)
  - src/places.py (Google Places API "New" client with pre-filter + auth error handling)
  - scripts/run_phase_1_discovery.py
  - scripts/run_cleanup.py
- Regions pre-configured: LA_City, LA_County, Greater_LA, Palm_Springs, Bakersfield,
  Phoenix, San_Diego, Bay_Area, Sacramento, Orange_County.
- Archive cleanup statuses: online_system_exclude, already_contacted, do_not_contact, closed_no_reply.
- Dev notes:
  - Ketan's venv is on Python 3.14; pgeocode works fine, uszipcode broke.
  - "Places API (New)" must be explicitly enabled in Google Cloud — separate from old "Places API".
  - Sheet tabs must match config.py exactly (case + underscores). Renamed tabs to:
    Leads, Already_Contacted, Coverage, Templates, No_Website_Schools, Archive.
  - places.py distinguishes auth errors (fatal, abort) from transient errors (continue).

### 2026-04-21 — Phase 1 first successful run
- First real run: zip 90045
- Result: 514 total places discovered, 428 written to Leads (status=pending_classify),
  51 to No_Website_Schools (status=collected), 35 pre-filtered skips.
- 5 categories hit 60-result cap: preschool, daycare, martial_arts, gymnastics, montessori.
  (Documented limitation; deferred fix.)
- Cost: ~$1-2 of Google Places API credit for one zip.
- Phase 1 complete for MVP purposes.

### Next session: Phase 2 — dedupe against Already_Contacted
Before running Phase 3 classification, we filter Leads against Already_Contacted:
- Match by email (case-insensitive exact) AND school_name (fuzzy match >= 90% similarity)
- Any match: row status → already_contacted
- Runs as a standalone script: `python scripts/run_phase_2_dedupe.py`
- Should be re-runnable idempotently (repeated runs produce same result)

### Known limitations carried into future phases
- 60-result cap per category/zip in Places API. Dense zips may miss schools.
  Fix candidates: split by sub-area, use different query phrasings per sub-category. Phase 9 problem.
- `process_zip` is not idempotent — re-running on a completed zip duplicates rows.
  Mitigation: use `process_region`, which skips completed zips.
- Cleanup script deletes rows one at a time (Sheets API batching complexity).
  Fine at small volumes; revisit if cleanup is run on 1000+ rows.