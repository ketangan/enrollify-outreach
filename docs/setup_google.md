# Google Cloud Reference

Main steps are in `PHASE_0_CHECKLIST.md`. This file has debugging info.

## What you're setting up

- **Places API** — for finding schools by location
- **Sheets API** — for reading/writing the data sheet
- **Service account** — lets scripts access Sheets without OAuth

All three go in the same Google Cloud project.

## Budget reality

- Google requires billing enabled even for free tier usage
- You get **$200/month free credit** auto-applied
- Our expected usage: **~$5-10/month** — you will not hit the free tier cap
- Budget alert at $5 = early warning if something goes wrong

## Common gotchas

- **"Places API" vs "Places API (New)"** — two products, different pricing. Use the **New** one. Our code targets the new endpoint.
- **Sheets API requires BOTH Sheets API AND Drive API enabled.** Not documented clearly. Enable both.
- **Service account JSON is a secret** — never commit it. `.gitignore` already excludes `config/*.json`.
- **Sharing the sheet** — must share with the `client_email` from the JSON, not your own email. Editor access required.
- **API key restrictions:** if you accidentally restrict to the wrong API, calls will fail with 403. Go back to Credentials → edit key → fix restrictions.

## Monitoring usage

After a week of use, check:
- **Billing → Reports** → actual costs (should be pennies)
- **APIs & Services → Metrics** → API call volume per service

Seeing 10,000+ calls/day on Places API with no output? Stop the script. Something's looping.

## Rate limits

- **Places API (New):** 300 requests/second (you'll never hit this)
- **Sheets API:** 60 requests/minute per user (code batches writes to stay under)

## If the budget alert fires

1. Stop running scripts immediately
2. Check **APIs & Services → Metrics** — which API is spiking?
3. Check **Billing → Reports** — is it a bug or legitimate usage?
4. Worst case: delete the API key to stop all usage, then investigate
