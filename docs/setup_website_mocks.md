# Website Mock Follow-Up Setup

This feature adds optional website-refresh mock links to follow-up emails for
schools you approve as good candidates. A conservative scanner can suggest
dated-site opportunities first, but suggestions are manual-gated.

Use a separate Cloudflare Workers static-assets app/subdomain for mocks. Do not
direct-upload only mock files into the manually managed `mypontora.com` website
project, because that can replace the production marketing site contents.

## Google Sheet Setup

Run this once:

```bash
python scripts/setup_website_mock_sheet.py
```

It appends these `Leads` columns:

```text
website_mock_candidate
website_mock_type
website_mock_versions
website_mock_status
website_mock_payload
website_mock_generated_at
website_mock_notes
```

It also adds a `Templates` row named:

```text
website_mock_followup_addendum
```

Edit that row in the Sheet if you want to adjust the follow-up P.S. copy.

## Cloudflare Setup

Create a new Cloudflare Workers static-assets app for generated mocks, for
example:

```text
pontora-mocks
```

Attach a subdomain such as:

```text
mocks.mypontora.com
```

Then add these GitHub Actions settings in the `enrollify-outreach` repo.

Repository variables:

```text
WEBSITE_MOCK_BASE_URL=https://mocks.mypontora.com
CLOUDFLARE_MOCKS_WORKER_NAME=pontora-mocks
```

Repository secrets:

```text
CLOUDFLARE_ACCOUNT_ID
CLOUDFLARE_API_TOKEN
```

The fastest token setup is Cloudflare's `Edit Cloudflare Workers` API token
template, scoped to the account that owns `pontora-mocks` and the
`mypontora.com` zone. At minimum, the token must be able to write Workers
scripts for the account.

## Manual Test

To scan active outreach rows and ready-to-send rows for likely dated-site
opportunities:

```bash
python scripts/suggest_website_mocks.py --dry-run --include-ready-to-send
python scripts/suggest_website_mocks.py --write-sheet --include-ready-to-send
```

This writes `website_mock_candidate=suggested` and
`website_mock_status=needs_review`. It does not generate pages and does not
change email copy.

Approve suggestions from In Progress or Review before the follow-up is due.
That changes the row to `website_mock_candidate=yes` and
`website_mock_status=not_started`.

After approving a lead as a mock candidate in Review or In Progress:

```bash
python scripts/generate_website_mocks.py --base-url https://mocks.mypontora.com --output-dir generated/website-mocks-site
```

Open `generated/website-mocks-site/index.html` locally to inspect the pages.

After Cloudflare deployment is configured, use:

```bash
python scripts/generate_website_mocks.py --base-url https://mocks.mypontora.com --write-sheet
```

To regenerate already-created mocks without creating daily outreach drafts, run
the GitHub Action named `Refresh Pontora Website Mocks` with `force=true`. This
rebuilds the static mock site, deploys it to Cloudflare, and refreshes the mock
metadata in the Leads sheet.

Follow-up drafts include the mock addendum only when:

```text
website_mock_candidate=yes
website_mock_status=generated
website_mock_payload contains at least one URL
```

`website_mock_payload` stores two URLs per mock: `url` is the tracked link used
inside customer follow-up drafts, while `preview_url` is the clean internal
review link used on `mocks.mypontora.com` and in the follow-up summary email.

In GitHub Actions, Gmail sync runs before the mock site is rendered so
`mocks.mypontora.com` can show current sent/draft status. The suggestion scan,
mock rendering, and mock deployment are intentionally non-blocking for the core
daily outreach run. If Cloudflare deployment fails, daily Gmail draft generation
still continues, and mock URLs are not written back to the Sheet.

The daily order is:

```text
suggest website mock opportunities
render/deploy approved mock candidates
write generated URLs back to Leads
create Gmail drafts/follow-ups
```

So the practical rule is simple: approve a suggestion before the morning daily
run that will create the follow-up. If you approve it after that run, the mock
can be generated on the next run instead.
