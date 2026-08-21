# Site Generator Setup (Durable Storage)

The full-site generator (`/site-generator` in the webapp) works with zero
setup beyond `SITE_GENERATOR_ACCESS_KEY` — but without the steps below, it
writes to local disk, which is fine on your laptop and **not durable on
Render** (the free tier recycles its container on idle/redeploy, wiping
anything written to disk). This doc covers the two pieces that make
generated sites durable: file storage (Cloudflare R2) and metadata (a
Google Sheet tab).

Both are optional individually — the app falls back to local disk / skips
Sheet recording when unconfigured — but you want both before sharing any
generated link with a prospect from a Render deployment.

## Part 1 — Cloudflare R2 (file storage)

### 1. Create the bucket

1. Cloudflare dashboard → **R2 Object Storage** → **Create bucket**.
2. Name it `pontora-generated-sites` (matches the default in `src/config.py` —
   use a different name only if you also set `R2_BUCKET_NAME` to match).
3. Location: Automatic. Leave the rest default.

### 2. Create an API token

1. R2 → **Manage R2 API Tokens** → **Create API Token**.
2. Permissions: **Object Read & Write**.
3. Scope it to the `pontora-generated-sites` bucket only (not account-wide).
4. Create it, and copy the three values shown once: **Access Key ID**,
   **Secret Access Key**, and your **Account ID** (also visible in the R2
   overview page sidebar). You can't view the secret again after leaving
   this page — if you lose it, delete the token and make a new one.

### 3. Deploy the router Worker (one-time)

The bucket alone has no public URL. A small Worker (`cloudflare/generated-sites/worker.js`
in this repo) serves files from it, resolving directory-style URLs to
`index.html` the same way the mocks site already does. You deploy this
**once** — after that, adding new generated sites is pure Python (uploading
objects to the bucket), no further Worker changes needed.

**Dashboard method (no Node required):**

1. Workers & Pages → **Create** → **Create Worker**.
2. Name it `pontora-generated-sites`, deploy the default template first.
3. Open it → **Edit code** (Quick Edit). Delete the placeholder code and
   paste in the contents of `cloudflare/generated-sites/worker.js` from this
   repo. Save and deploy.
4. Worker → **Settings** → **Bindings** → **Add binding** → **R2 Bucket**.
   Variable name: `BUCKET` (must match exactly — the script reads
   `env.BUCKET`). Bucket: `pontora-generated-sites`. Save.

**CLI method (if you have Node + wrangler):**

```bash
cd cloudflare/generated-sites
npm install -g wrangler   # if you don't have it
wrangler login
wrangler deploy
```

`wrangler.toml` in that directory already declares the `BUCKET` binding, so
the CLI path needs no extra dashboard steps.

### 4. Attach a public domain

1. Worker → **Settings** → **Domains & Routes** → **Add** → **Custom Domain**.
2. Enter something like `sites.mypontora.com` (parallel to `mocks.mypontora.com`).
3. Cloudflare adds the DNS record automatically if the domain's zone is
   already on your Cloudflare account (it is, since `mypontora.com` already
   is). Wait for the certificate to provision (usually under a minute).

### 5. Set the environment variables

Locally (`.env`) and on Render (service → Environment):

```
R2_ACCOUNT_ID=<from step 2>
R2_ACCESS_KEY_ID=<from step 2>
R2_SECRET_ACCESS_KEY=<from step 2>
R2_BUCKET_NAME=pontora-generated-sites
GENERATED_SITES_BASE_URL=https://sites.mypontora.com
```

That's it — `src/r2_storage.py` picks these up automatically.
`generate_full_site.py` checks `r2_storage.is_configured()` before every
run and uses R2 when all four are set, local disk otherwise. No code
changes needed to switch between them.

## Part 2 — Google Sheet metadata

No manual setup needed. The first time anything writes to it, a
`Generated_Sites` tab is created automatically in your existing outreach
Sheet (same `GOOGLE_SHEET_ID` you already use), with headers matching
`src/site_generator_state.py`'s `HEADERS` constant. One row per
(business, theme, version) — regenerating a theme adds a row, it never
edits or deletes an existing one.

## Verifying it worked

```bash
python scripts/generate_full_site.py \
  --name "Some Real Business" --category music --city "Your City" --state CA \
  --record-to-sheet \
  --base-url irrelevant-when-r2-is-configured \
  --output-dir generated/full-sites
```

Check for:
- Log lines showing `https://sites.mypontora.com/sites/...` URLs (not
  `/mocks/...` on a local host) — confirms R2 was used.
- Those URLs actually load in a browser, including photos.
- A new row in the `Generated_Sites` tab of your Sheet.

If R2 env vars are missing or wrong, the script logs a clear error from
`r2_storage.R2NotConfiguredError` / a boto3 auth error rather than silently
falling back — check the log if a run that should be using R2 produces
`/mocks/` URLs instead, since that means it silently fell back to disk
(most likely `GENERATED_SITES_BASE_URL` or one of the R2 vars is unset).

## What "durable" actually buys you

Once both parts are configured, the sequence for a webapp-submitted job is:
generate → upload files to R2 (survives the webapp process being recycled)
→ write the Sheet row (also survives) → the subprocess exits. Nothing that
needs to last is ever left waiting on the webapp process's local disk —
the same principle the outreach mock-site pipeline already relies on via
Cloudflare Workers + the Leads sheet.
