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

### 2026-04-21 — Phase 2 delivered
- scripts/run_phase_2_dedupe.py: dedupes Leads against Already_Contacted
- Matching: normalized website (primary) + 90% fuzzy name match (fallback)
- Added rapidfuzz to requirements.txt
- Dry-run default; --commit to apply status=already_contacted
- First run against 90045: 0 matches (expected — contacted list covers different zips)
- Bug found & fixed during setup: Leads tab column 11 header was "pending_classify"
  (the default value) instead of "status". Renamed header.

### Phase status
- Phase 0: DONE
- Phase 1: DONE
- Phase 2: DONE
- Phase 3 next: enrollment method classification via Claude Haiku

### 2026-04-21 — Phase 3 delivered
- src/fetcher.py: website fetching + HTML cleanup (homepage + enrollment sub-pages)
- src/classifier.py: 3-stage classifier (pre-filter → local keywords → Haiku)
- scripts/run_phase_3_classify.py: CLI with --zip, --limit, --dry-run flags
- scripts/fix_status.py, scripts/reset_for_reclassify.py: one-off cleanup utilities
- Added beautifulsoup4 to requirements.txt
- Prompt tuned once after first run (was too conservative, was flagging ~45% for manual review)
- Prompt caching enabled for Haiku calls

### Phase 3 first run results (20 leads, zip 90045)
- 11 online_system_exclude, 3 ready_for_owner_lookup, 6 needs_manual_review
- Decision source: 2 local, 16 LLM, 2 fetch_failed
- Cost estimate: ~$0.03 for 20 leads

### Known issues
- Opus1 (opus1.io) vendor not in skip_lists — add before next run
- ~10-15% of sites fetch-fail (404, 403, cookie walls) — acceptable

### Phase status
- Phase 0: DONE
- Phase 1: DONE
- Phase 2: DONE
- Phase 3: DONE (small sample; full 430 can run anytime)
- Phase 4 next: Owner discovery + email guessing


### 2026-04-22 — Phase 4 delivered
- src/owner_finder.py: extracts emails via regex + mailto links, Haiku picks best owner + email
- scripts/run_phase_4_owners.py: processes ready_for_owner_lookup leads
- Fixes during development:
  - Removed footer/header from NOISE_TAGS — they contain legit contact info
  - Extract emails from mailto: hrefs, not just text
  - Hard safety: empty best_email ALWAYS → needs_manual_review regardless of LLM confidence
- First run: 2/3 qualified advanced to ready_to_send, 1 to manual review (genuinely no email)

### Known issue (accepted)
- Phase 3 sometimes misclassifies schools with embedded inquiry forms as contact_form_qualify
  when they should be online_system_exclude (e.g. Music Teacher LA's /request-info/ form).
  Caught by human review in Phase 5. Not a fix target for now.

### Phase status
- Phases 0-4: DONE
- Phase 5 next: draft generation + morning approval flow

### 2026-04-22 — Phase 5 delivered
- src/zoho.py: IMAP APPEND to Drafts folder, SMTP send for summary emails
- src/drafter.py: template rendering with {{placeholder}} substitution from Templates tab
- scripts/run_phase_5_drafts.py: full CLI with --dry-run, --limit, --no-summary
- scripts/preview_draft.py, scripts/reset_phase5.py: debug utilities
- Python 3.14 compat fix: imaplib.Time2Internaldate requires timezone-aware datetime
- First real run: 2 drafts uploaded to Zoho successfully, approval email received

### Known limitation (carried forward)
- Category per lead is set by first matching Phase 1 category query.
  Multi-discipline schools (e.g. "arts academy" surfaced via music query)
  end up with a single category. Causes template to say "music schools like yours"
  when the school is actually multi-discipline. Caught manually in Phase 5 approval
  review. Not worth fixing for MVP.

### Phase status
- Phases 0-5: DONE
- Phase 6 next: follow-up scheduling + reply detection (Pushover alerts)

### 2026-04-22 — Phase 6 delivered
- src/zoho_sync.py: IMAP fetch for Sent + Inbox, threaded-reply builder
- scripts/run_phase_6_sync.py: reconciles Sent folder + detects replies, emails alerts
- scripts/run_phase_6_followup.py: drafts threaded follow-ups using In-Reply-To headers
- First sync run: 2 sent emails detected from prior manual send, message_ids captured
- Reply alerts go to Ketan's own email (skipped Pushover for MVP)
- Follow-up script creates proper threaded replies (not fresh emails)

### Phase status
- Phases 0-6 DONE. MVP complete.
- Phases 7-9 are improvements (coverage automation, mobile UI, cron)

### 2026-04-22 — Phase 5 + 6 + daily orchestrator delivered
- scripts/run_daily.py: orchestrator that runs sync → follow-up drafts → initial drafts in sequence
- Daily routine: `python scripts/run_daily.py` in the morning, review Zoho Drafts, send approved
- Sub-phases are independently skippable (--skip-sync, --skip-followup, --skip-drafts)
- Failures in early steps don't block later steps — drafts still get created if sync has issues
- Generates 1-3 summary emails per run depending on what happened

### Daily loop (MVP operating model)
1. Morning: `python scripts/run_daily.py`
2. Review Zoho Drafts folder, click send on approved ones
3. Respond immediately to any reply alerts
4. When leads run low: manually run Phase 1/3/4 on a new zip

### Phase status
- Phases 0-6: DONE. MVP shipped.
- Phases 7-9: deferred (not built until operating data justifies them)