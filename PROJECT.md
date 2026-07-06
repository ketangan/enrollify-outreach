# Enrollify Outreach — Project Source of Truth

> **⚠️ READ THIS FIRST if you're Claude in a new session.**
> This document IS the memory. Ketan pastes it at the start of every session. Before closing any session, output an updated version in a code block for Ketan to commit. Every decision, schema change, or tool swap must be reflected here. If ambiguous, ask — don't guess.

**Last updated:** 2026-07-06
**Status:** MVP shipped (Phases 0–6). Phases 7–9 done. Phase 8 webapp live at https://enrollify-admin.onrender.com. Auth still pending.
**Repo:** private GitHub repo `enrollify-outreach`

---

## Who & what

- **Owner:** Ketan Gandhi, co-founder of Enrollify (enrollifyapp.com)
- **Co-founder:** Natasha Julka (on the website, but NOT named in cold outreach — team size deliberately not disclosed)
- **Goal:** Automate cold outreach to small activity-based schools (dance, music, preschool, sports, etc.) without online enrollment systems
- **Target volume:** ~20–25 emails/day
- **Budget cap:** $20/mo ideal, $100/mo absolute max
- **Geographic start:** LA area (90045) → California → US

## Working agreement with Claude

- Claude has no memory between sessions. This file IS the memory.
- **Session protocol:** Ketan pastes this file → states task → Claude confirms → at session end Claude outputs updated PROJECT.md.
- **Tone:** direct, no flattery, push back when Ketan is wrong. Ketan is a senior engineer — skip hand-holding on code.

---

## Architecture decisions (locked in)

| Area | Decision | Why |
|---|---|---|
| Data store | Google Sheets | Visibility, mobile-accessible, manual edit friendly. DB migration deferred until 1+ paying customer. |
| LLM | Claude Haiku via Anthropic API | Cheapest capable model |
| Scraping | Google Places API (New) | Free within $200/mo credit, structured, no ToS risk |
| Email | Zoho Mail Lite ($1/mo) | Free tier doesn't have IMAP; Lite does |
| Mail protocols | IMAP (drafts) + SMTP (send) | |
| Webapp | FastAPI + Jinja2 + HTMX on Render free tier | No React. Reuses `src/*` modules directly. |
| Runtime | Python 3.14 local (Mac), 3.12.7 on Render | GitHub Actions cron at 14:30 UTC Mon–Fri runs `run_daily.py` |
| Secrets | `.env` locally, GitHub repo secrets + Render Secret File in prod | |

**Tools rejected:** Clay/Apollo/Hunter (cost), Apify (complexity), Google Workspace (overkill), second outreach domain (until 5+ paying customers), AI-generated `specific_observation` (static per-method paragraphs are simpler/cheaper), pick-and-choose plan tier (decision paralysis + business-ops complexity; revisit when there's a real customer asking for it).

---

## Target school categories

Dance studios, music schools, sports academies, pre-schools, day-cares, martial arts, art studios, gymnastics/cheer, swim schools, tutoring/learning centers (independents only), language schools for kids, coding/STEM for kids, Montessori (independents).

**Disqualification → `online_system_exclude`:** has online enrollment form, login/portal button, or uses known third-party enrollment software (ClassDojo, Jackrabbit, DanceStudio-Pro, Brightwheel, Procare, Mindbody, iClassPro, etc.).

When uncertain: mark `needs_enrollment_system_classification`, never auto-email.

**Schools without websites** are not discarded — collected into `No_Website_Schools` tab with reviews for **Phase 10 (deferred)**: website-builder upsell, not built until Enrollify has 10+ paying customers.

---

## Google Sheet schema

Spreadsheet: `Enrollify Outreach`. Service account has Editor access.

### Tab: `Leads`

```
id | name | website | category | city | state | zip | phone | address |
discovered_date | status | enrollment_method | owner_name | owner_title |
owner_source_url | best_email | email_confidence | last_action |
sent_at | sent_message_id | follow_up_at | follow_up_sent_at | replied_at |
do_not_contact_reason | notes
```

### Status enum (current)

```
pending_classify                          → discovered, awaiting Phase 3
needs_enrollment_system_classification    → Phase 3 fallback (user picks enrollment_method)
online_system_exclude                     → disqualified
ready_for_owner_lookup                    → classified, awaiting Phase 4
needs_owner_review                        → Phase 4 fallback (user fills owner+email)
ready_to_send                             → draft-ready
awaiting_approval                         → draft in Zoho, awaiting send
sent                                      → initial email sent
follow_up_sent                            → (unused — handled by follow_up_sent_at timestamp on sent rows)
replied                                   → 🚨 human reply received
bounced                                   → mailer-daemon bounce detected
closed_no_reply                           → 7 days post-follow-up, no reply, auto-archived
already_contacted                         → matched against Already_Contacted or Archive on dedupe
do_not_contact                            → manual or internal_duplicate
no_website_collected                      → Phase 10 holding bin
```

### Enrollment method enum

```
contact_form_qualify, email_qualify, pdf_form_qualify,
third_party_form_qualify, online_system_exclude, needs_manual_review
```

### Email confidence enum

```
high, medium, low, unverified, manual
```

### Other tabs

- **`Already_Contacted`** — Ketan's prior outreach. `school_name | email | contacted_date | outcome | notes`
- **`Coverage`** — `zip | city | total_found | qualified | contacted | replied | status | started_date | completed_date | admin`
- **`Templates`** — `template_id | subject | body | observation | last_updated`. Rows: `contact_form`, `email`, `pdf_form`, `third_party_form`, `follow_up`
- **`No_Website_Schools`** — Phase 10 holding bin with `google_reviews_json | yelp_reviews_json` etc.
- **`Archive`** — moved here by `run_cleanup.py` from archivable statuses (online_system_exclude, do_not_contact, closed_no_reply, bounced, already_contacted)

---

## Pipeline phases & files

| Phase | Script(s) | Purpose |
|---|---|---|
| 1 — Discovery | `scripts/run_phase_1_discovery.py` | Google Places → `Leads` (pending_classify). Auto-runs `dedupe_within_leads` after each zip. |
| 1.5 — Internal dedupe | `src/dedupe_within_leads.py` | Demotes same-school-different-zip duplicates to `do_not_contact` |
| 2 — Contact dedupe | `scripts/run_phase_2_dedupe.py` | Dedupes against Already_Contacted + Archive. Marks matches `already_contacted`. |
| 3 — Classify | `scripts/run_phase_3_classify.py` | URL prefilter → keyword scan → Haiku. Sets enrollment_method or escalates to `needs_enrollment_system_classification`. |
| 4 — Owner lookup | `scripts/run_phase_4_owners.py` | Fetch about/team pages → regex emails → Haiku picks owner+email. Stage-2 web-search fallback if Stage 1 finds no name. Failures → `needs_owner_review`. |
| 5 — Draft | `scripts/run_phase_5_drafts.py` | Renders template → IMAP-appends to Zoho Drafts → emails Ketan a daily summary with pipeline state. |
| 6 — Sync | `scripts/run_phase_6_sync.py` | Reads Zoho Sent (marks `sent`) + Inbox (replies → `replied` + 🚨 alert; bounces → `bounced` + 📭 alert). |
| 6b — Follow-up draft | `scripts/run_phase_6_followup.py` | Threaded follow-up drafts after 7 days. |
| Lifecycle | `scripts/run_close_stale.py` | Sent + no reply ≥ 7 days post-follow-up → `closed_no_reply`. |
| Cleanup | `scripts/run_cleanup.py` | Move archivable statuses from `Leads` → `Archive`. Idempotent (dedupes by id). Rate-limited at 30 writes/min. |
| Migration (one-off) | `scripts/migrate_manual_review.py` | Already run. Split legacy `needs_manual_review` into the two new statuses. |
| Daily orchestrator | `scripts/run_daily.py` | sync → followup → owners → drafts. GitHub Actions runs at 14:30 UTC Mon–Fri. |
| Admin one-offs | `scripts/audit_drafts.py`, `scripts/list_drafts_with_leads.py` | Audit Zoho Drafts against Leads+Archive (flags duplicates before send). |

---

## Webapp (Phase 8) — live at https://enrollify-admin.onrender.com

Routes:
- `/` — pipeline diagram, per-stage pending counts, recommendations, banner alerts (low-queue, review-edits-need-rerun), running-jobs status bar
- `/coverage` — region progress table
- `/leads` — filterable list (status/zip/category)
- `/review` — three tabs (classify / owner / pre_send). Top: one record at a time (mobile-friendly). Bottom: paginated grid with inline edits. Banner when review edits need downstream rerun.
- `/jobs`, `/jobs/{id}` — subprocess job status, scroll-preserving auto-refresh, cancel button (SIGTERM)

Key modules:
- `webapp/webapp/dashboard.py` — stage counts, pipeline alerts (list), `count_review_edited_pending_rerun()`
- `webapp/webapp/jobs_runner.py` — subprocess spawner; stale-job cleanup on uvicorn start
- `webapp/webapp/routes_actions.py` — POST handlers for action buttons

**Critical TODO:** no auth on the webapp. Anyone with the URL can trigger sends, spend Places API credit, mark leads do-not-contact. Don't share the URL publicly.

---

## Secrets (`.env`)

```
ANTHROPIC_API_KEY=
GOOGLE_PLACES_API_KEY=
GOOGLE_SHEETS_CREDENTIALS_PATH=./config/google-service-account.json
GOOGLE_SHEET_ID=
ZOHO_EMAIL=ketan@enrollifyapp.com
ZOHO_APP_PASSWORD=
ZOHO_IMAP_HOST=imap.zoho.com
ZOHO_IMAP_PORT=993
ZOHO_SMTP_HOST=smtp.zoho.com
ZOHO_SMTP_PORT=465
PUSHOVER_USER_KEY=
PUSHOVER_APP_TOKEN=
DEFAULT_DAILY_EMAIL_CAP=20
WORKING_HOURS_START=9
WORKING_HOURS_END=17
TIMEZONE=America/Los_Angeles
HOME_ZIP=90045
```

In production (Render): same vars as env vars except Google credentials live at `/etc/secrets/google-service-account.json`.
In GitHub Actions: same as Render, credentials as `GOOGLE_SHEETS_CREDENTIALS_JSON` secret.

---

## Email templates (current — Templates tab)

**Subject (initial):** `Reimagining enrollment for smaller schools`
**Subject (follow-up):** `Re: Reimagining enrollment for smaller schools`

**Observations (per method, static):**
- `contact_form` — `I was on {{school_name}}'s site earlier and noticed that families interested in signing up are asked to fill out a contact form to get started.`
- `email` — `I came across {{school_name}}'s website and saw that prospective families are directed to email the school directly to begin enrollment.`
- `pdf_form` — `I was browsing {{school_name}}'s website and noticed enrollment starts with a downloadable PDF form that families fill out and return.`
- `third_party_form` — (matches schools using Google Forms / Jotform / Typeform / Formstack / Wufoo / Cognito Forms)

**Body (shared):**
```html
Hi {{owner_first_name}},

{{specific_observation}}

We've been building Enrollify — enrollment software designed specifically for {{category}} schools like yours, where the big platforms are overkill and scheduling tools treat enrollment as an afterthought.

Here's what's included:
- Custom-built enrollment forms tailored to your programs and branding
- A clean dashboard where every submission lands organized and searchable
- Built-in reporting on enrollment trends and application activity
- Lead management so prospective families don't slip through the cracks
- AI-generated summaries of each applicant, scored against your criteria
- One-click exports to Brightwheel and other tools you may already use
- Zero setup on your end — no servers, no databases, no maintenance

If you'd like to see it in action, I can send over a custom enrollment form built specifically for {{school_name}}, ready to try. No call required, no commitment. If you like it, we'll set you up with an extended free trial on the full platform — everything unlocked. If it's not for you, export your data and walk away. No questions asked.

A bit of context: Enrollify is built by a team with decades of experience shipping software at companies large and small, and with direct experience running enrollment for online schools — which is where the idea came from.

Happy to send one over if you'd like to see it.

Thanks,
Ketan
enrollifyapp.com
```

**Follow-up body:** "Just wanted to follow up in case my note from last week got buried. We've been working on a small demo inspired by {{school_name}}'s website…"

---

## Deliverability state (2026-05-26)

- DMARC at `p=quarantine`, `pct=100`, `rua=mailto:ketan@enrollifyapp.com`
- SPF: `v=spf1 include:zoho.com ~all` ✓
- DKIM: `zoho._domainkey` verified ✓
- Zoho outbound IP `136.143.188.16` was on SpamCop; cleared after 48hrs + Zoho support ticket
- Currently listed on s5h.net only (low impact)
- **Cold email response problem is now real signal, not sample-size noise.** As of 2026-07-06:
  - Initial sends: 595
  - Follow-up sends: 445
  - Unique recipients: 575
  - Brent and Carmen are no longer valid leads; no usable response came from them.
  - Stop treating volume as the bottleneck. Keep checking deliverability, but the likely failure is offer, targeting, channel, trust surface, or copy.

---

## Known limitations & deferred work

- **60-result Places API cap per category/zip.** ~70% of LA zips process as `partial_complete`. Fix candidates exist (sub-area splits, narrower queries) but rejected — risk of missing multi-category schools outweighs recovery.
- **No auth on webapp.** Highest-priority outstanding work. Don't share URL until fixed.
- **No "retry Phase 4 lookup" button.** Editing a lead in Review's classify tab marks it `ready_for_owner_lookup`; user must click Run Downstream from dashboard. Auto-running on save rejected (cost surprise + latency + failure handling).
- **DB migration deferred** until 1+ paying customer. All sheet operations rate-limited at 60 writes/min/user; batched updates are mandatory (`update_cell` loops will fail on >30 rows).
- **Phase 10 (no-website upsell)** deferred until 10+ paying customers.
- **Multi-discipline schools** get a single `category` from Phase 1's first matching query. Template may say "music schools like yours" when school does music + dance. Caught in Phase 5 review, not worth fixing.
- **Phase 4 ~30-40% lost-at-email-step.** Stage 2 web search recovers some; remaining hit `needs_owner_review` and need manual lookup.

---

## Operational notes

- **Daily routine:** GitHub Actions runs `run_daily.py` at 14:30 UTC. Ketan reviews Zoho Drafts, sends approved. Responds to reply alerts.
- **When pending_classify > 100 and ready_to_send < 10:** banner appears on `/`. Click Run Downstream.
- **When new zip needed:** webapp Phase 1 buttons or CLI `python scripts/run_phase_1_discovery.py --next --region LA_City` / `--auto --region X --max-zips N --max-api-calls 500`.
- **Weekly cadence:** run `close_stale.py --commit` then `run_cleanup.py --commit` to age out dead leads and archive disqualified ones.
- **Pipeline order in daily run:** sync → followup → owners → drafts.
- **Sheet rate-limit:** if you see APIError 429, the script needs batched updates (`worksheet.batch_update` with 50-cell chunks + sleep), not `update_cell` in a loop. Affects close_stale, cleanup, dedupe, phase_2, phase_4. All current scripts are batched.
- **Click tracking review (Mondays):** `python scripts/show_clicks.py --since-days 7` — surfaces schools that clicked the demo link in follow-ups but didn't reply. These are in-person visit candidates.
- **Click tracking infra:** Apps Script web app receives POSTs from demo.html on real user gestures (mouse/scroll/key/touch within 8s of page load). Writes to `Click_Log` tab. `{{lead_id}}` placeholder in follow_up template renders `?utm_content=<lead_id>` in URL. Apps Script URL is hardcoded in demo.html and committed to ketangan/enrollify-website repo. If abused, redeploy Apps Script → update URL → re-upload demo.html → commit. Drafter's `_first_name()` now strips honorifics ("Dr. Sarah" → "Sarah").

---

## Working priorities (most recent → oldest)

1. **Fix the response problem before scaling more unchanged cold email.** 575 unique recipients is enough to reject the "not enough data" explanation. Run controlled experiments on offer, target segment, CTA, proof, and channel.
2. **Mondays: review click data, visit top clickers in person.** Treat in-person follow-up as an experiment, not a proven channel yet. Brent and Carmen are no longer valid conversion evidence.
3. **Cloudflare consolidation (Path A).** `round-bread-580b` (static-assets Worker) + `enrollify-website` (orphan Worker connected to GitHub repo) → one Cloudflare Pages project hooked to ketangan/enrollify-website. Result: `git push` deploys instead of drag-and-drop. Do on a weekend when uninterrupted. Until then, every site update requires re-upload via Cloudflare dashboard.
4. **Auth on webapp.** Still the biggest tech gap.
5. **Field a real customer before any new tier/pricing changes.**
6. **DB migration after 1+ paying customer.**

Deferred (do not build without explicit request):
- Phase 10 upsell, pick-and-choose pricing tier, retry-Phase-4 button, multi-discipline category fix, Yelp scraping, capped-category narrower re-queries
- Third demo video (current 2 demos suffice; Veo can't render full marketing videos)
- Server-side click tracking via Cloudflare Worker (route-shadowed by static-assets Worker; client-side JS approach won)
- Adding demo link / click tracking to initial templates (only follow_up tracks for now; revisit after seeing follow-up click data)
- Click_Log cleanup/archival (Google Sheets handles millions of rows; revisit at 10k+ entries)

---

## Recently shipped (this week)

- **Click tracking via Apps Script** — gesture-filtered, written to Click_Log tab, schema matches `show_clicks.py` reader. `{{lead_id}}` substitution in drafter.
- **Webapp common tasks cheat sheet** on home page — 17 collapsible tasks with code blocks, dry-run guidance, deliverability checks.
- **Phase 6 sync** — unthreaded bounce detection + 4xx code matching. Caught 6 new bounces on first run.
- **audit_drafts.py** — follow-up-aware duplicate detection (don't flag legitimate Re: follow-ups).
- **/leads search** + Edit↗ button to /review with auto-mode-pick.
- **Grid DNC button** on /review bottom grid.
- **Mail-tester 10/10 deliverability confirmed.** Pitch is the bottleneck now, not delivery.
- **dry-salad-8b7e Cloudflare Worker deleted** — was route-shadowed, never wrote a row, dead code.
