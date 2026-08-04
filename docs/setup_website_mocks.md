# Website Mock Follow-Up Setup

This feature adds optional website-refresh mock links to follow-up emails for
schools you manually mark as good candidates.

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

After marking a lead as a mock candidate in Review or In Progress:

```bash
python scripts/generate_website_mocks.py --base-url https://mocks.mypontora.com --output-dir generated/website-mocks-site
```

Open `generated/website-mocks-site/index.html` locally to inspect the pages.

After Cloudflare deployment is configured, use:

```bash
python scripts/generate_website_mocks.py --base-url https://mocks.mypontora.com --write-sheet
```

Follow-up drafts include the mock addendum only when:

```text
website_mock_candidate=yes
website_mock_status=generated
website_mock_payload contains at least one URL
```

In GitHub Actions, mock rendering/deployment is intentionally non-blocking for
the core daily outreach run. If Cloudflare deployment fails, daily Gmail draft
generation still continues, and mock URLs are not written back to the Sheet.
