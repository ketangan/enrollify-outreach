# Enrollify Outreach

Automated cold-outreach pipeline for Enrollify — targets small activity-based schools without online enrollment systems, classifies them, finds owner contacts, drafts personalized emails, and sends with human approval.

**Source of truth:** [`PROJECT.md`](./PROJECT.md) — always read this first.

## Stack

- Python 3.11+
- Claude Haiku (Anthropic API) for classification & drafting
- Google Places API for lead discovery
- Google Sheets as data store
- Zoho Mail for sending
- Pushover for reply notifications

## Quick start

See `docs/` for detailed setup. High-level:

1. `cp .env.example .env` and fill in secrets
2. `pip install -r requirements.txt`
3. Create the Google Sheet (schema in `PROJECT.md`)
4. Run `python scripts/run_phase_1_discovery.py --zip 90045`

## Daily loop (once all phases done)

```
python scripts/run_daily.py
```

Runs the full pipeline, puts drafts in Zoho, emails Ketan the approval summary. Ketan reviews drafts in Zoho, clicks send.

## Phases

See `PROJECT.md` — work is structured as 10 phases (0–9). Don't skip ahead.
