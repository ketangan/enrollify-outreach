# Pontora Outreach

Automated cold-outreach pipeline for Pontora. It targets small activity-based schools without online enrollment systems, classifies leads, finds owner contacts, drafts personalized Gmail messages, and waits for Ketan to review/send manually.

**Source of truth:** [`PROJECT.md`](./PROJECT.md) — read this first for current architecture, phase status, and recent decisions.

## Stack

- Python 3.11+
- Claude Haiku 4.5 (Anthropic API) — classification, drafting, web-search owner finder
- Google Places API (New) — lead discovery
- Google Sheets — data store (Leads, Coverage, Templates, Already_Contacted, No_Website_Schools, Archive)
- Gmail API — draft creation, draft audit, sent/reply/bounce sync
- GitHub Actions — daily scheduled runs (`.github/workflows/daily.yml`)

## Quick Start

1. `cp .env.example .env` and fill in secrets.
2. `pip install -r requirements.txt`
3. In Google Auth Platform -> Audience, add `ketan@mypontora.com` as a test user.
4. Download Gmail OAuth Desktop client JSON to `config/gmail-oauth-client.json`.
5. Run `python scripts/setup_gmail_oauth.py` and authorize `ketan@mypontora.com`.
6. Verify access: `python scripts/run_phase_1_discovery.py --list-regions`
7. Verify templates: `python scripts/check_template_brand.py`

## Daily Loop

Runs automatically via GitHub Actions Mon-Fri at 14:30 UTC. Manual trigger:

```bash
python scripts/run_daily.py
```

Pipeline order: sync replies -> audit-gated follow-ups -> owner lookup -> audit-gated initial drafts. Drafts land in Gmail. Ketan reviews and clicks send.

## Lead Discovery

Auto-pick the closest uncompleted zip in a region:

```bash
python scripts/run_phase_1_discovery.py --next --region LA_County
```

Loop multiple zips with a hard cost cap:

```bash
python scripts/run_phase_1_discovery.py --auto --region LA_County --max-zips 5
```

After Phase 1, run downstream on the new leads:

```bash
python scripts/run_phase_2_dedupe.py --commit
python scripts/run_phase_3_classify.py
python scripts/run_phase_4_owners.py
```

## Coverage Dashboard

```bash
python scripts/run_phase_1_discovery.py --coverage --region LA_County
python scripts/run_phase_1_discovery.py --coverage
```

## Multi-Admin

Set `PONTORA_ADMIN` in `.env` or pass `--admin NAME`. The legacy `ENROLLIFY_ADMIN` env var still works as a fallback during migration.

## Phases

| # | Name | Status |
|---|------|--------|
| 0 | Setup | DONE |
| 1 | Lead discovery | DONE |
| 2 | Dedupe vs Already_Contacted | DONE |
| 3 | Enrollment method classification | DONE |
| 4 | Owner + email discovery | DONE |
| 5 | Gmail draft generation | DONE |
| 6 | Gmail sent/reply/bounce sync + follow-ups | DONE |
| 7 | Coverage tracking + zip expansion automation | DONE |
| 8 | Webapp admin UI | DONE, auth pending |
| 9 | GitHub Actions cron | DONE |
| 10 | Website-builder upsell to no-website schools | Deferred indefinitely |

## Project Structure

```text
.
├── config/
│   └── regions.yaml
├── docs/
├── scripts/
│   ├── setup_gmail_oauth.py
│   ├── run_daily.py
│   ├── run_phase_1_discovery.py
│   ├── run_phase_2_dedupe.py
│   ├── run_phase_3_classify.py
│   ├── run_phase_4_owners.py
│   ├── run_phase_5_drafts.py
│   ├── run_phase_6_followup.py
│   ├── run_phase_6_sync.py
│   └── reset_*.py
└── src/
    ├── classifier.py
    ├── coverage.py
    ├── drafter.py
    ├── fetcher.py
    ├── gmail_client.py
    ├── owner_finder.py
    ├── owner_web_search.py
    ├── places.py
    ├── regions.py
    ├── sheets.py
    └── skip_lists.py
```
