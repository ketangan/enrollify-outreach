# Enrollify Outreach

Automated cold-outreach pipeline for Enrollify — targets small activity-based schools without online enrollment systems, classifies them, finds owner contacts, drafts personalized emails, and sends with human approval.

**Source of truth:** [`PROJECT.md`](./PROJECT.md) — always read this first for current architecture, phase status, and recent decisions.

## Stack

- Python 3.11+
- Claude Haiku 4.5 (Anthropic API) — classification, drafting, web-search owner finder
- Google Places API (New) — lead discovery
- Google Sheets — data store (Leads, Coverage, Templates, Already_Contacted, No_Website_Schools, Archive)
- Zoho Mail — outbound sends + reply detection
- GitHub Actions — daily scheduled runs (`.github/workflows/daily.yml`)

## Quick start

1. `cp .env.example .env` and fill in secrets (ANTHROPIC_API_KEY, GOOGLE_PLACES_API_KEY, GOOGLE_SHEETS_CREDENTIALS_JSON, GOOGLE_SHEET_ID, ZOHO_APP_PASSWORD, HOME_ZIP)
2. `pip install -r requirements.txt`
3. Verify access: `python scripts/run_phase_1_discovery.py --list-regions`

## Daily loop

Runs automatically via GitHub Actions Mon–Fri at 14:30 UTC. Manual trigger:

    python scripts/run_daily.py

Pipeline order: sync replies → audit-gated followups → owner lookup → audit-gated initial drafts. Drafts land in Zoho. Ketan reviews and clicks send.

## Lead discovery — when current zips are running low

Auto-pick the closest uncompleted zip in a region:

    python scripts/run_phase_1_discovery.py --next --region LA_County

Loop multiple zips with a hard cost cap:

    python scripts/run_phase_1_discovery.py --auto --region LA_County --max-zips 5

Each zip uses ~50–100 Places API calls (~$0.25–$0.50). Default cap: 500 calls/run.

After Phase 1, run the remaining phases on the new leads:

    python scripts/run_phase_2_dedupe.py --commit
    python scripts/run_phase_3_classify.py
    python scripts/run_phase_4_owners.py

## Coverage dashboard

Show progress for a region:

    python scripts/run_phase_1_discovery.py --coverage --region LA_County

Show all regions:

    python scripts/run_phase_1_discovery.py --coverage

## Multi-admin (e.g. collaborator running Oregon)

Set `ENROLLIFY_ADMIN` in their `.env` or pass `--admin NAME`. Rows they create in Coverage will be tagged with their name. They can `--next`/`--auto` on a region you're not touching without collision (concurrency is enforced via `in_progress` status in the Coverage tab).

## Available regions

    python scripts/run_phase_1_discovery.py --list-regions

Defined in `config/regions.yaml`. Add new regions there.

## Phases (10 total)

| # | Name | Status |
|---|------|--------|
| 0 | Setup | DONE |
| 1 | Lead discovery | DONE |
| 2 | Dedupe vs Already_Contacted | DONE |
| 3 | Enrollment method classification | DONE |
| 4 | Owner + email discovery (Stage 1 site scrape + Stage 2 web search) | DONE |
| 5 | Draft generation + approval email | DONE |
| 6 | Follow-up + reply detection | DONE |
| 7 | Coverage tracking + zip expansion automation | DONE |
| 8 | Mobile approval UI | Deferred until webapp |
| 9 | GitHub Actions cron | DONE |
| 10 | Website-builder upsell to no-website schools | Deferred indefinitely |

Webapp dashboard (next major build) — see PROJECT.md.

## Project structure

    .
    ├── config/
    │   └── regions.yaml          # Named regions for Phase 1
    ├── docs/                      # Setup instructions per integration
    ├── scripts/                   # Phase runners + debugging
    │   ├── run_daily.py
    │   ├── run_phase_1_discovery.py
    │   ├── run_phase_2_dedupe.py
    │   ├── run_phase_3_classify.py
    │   ├── run_phase_4_owners.py
    │   ├── run_phase_5_drafts.py
    │   ├── run_phase_6_followup.py
    │   ├── run_phase_6_sync.py
    │   └── reset_*.py             # Recovery scripts
    └── src/
        ├── classifier.py          # Phase 3
        ├── coverage.py            # Coverage tab access (Phase 7)
        ├── drafter.py             # Phase 5
        ├── fetcher.py             # Website scraping
        ├── owner_finder.py        # Phase 4 Stage 1
        ├── owner_web_search.py    # Phase 4 Stage 2
        ├── places.py              # Phase 1 Google Places client
        ├── regions.py             # Zip → region resolution
        ├── sheets.py              # Google Sheets I/O
        ├── skip_lists.py          # Chain/franchise filters
        └── zoho.py                # Zoho IMAP/SMTP
