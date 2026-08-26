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

## Part 3 — Short links (optional, for the text-message box)

Every generated concept also gets a short link
(`https://sites.mypontora.com/p/ab12cd`) for the SMS-style message box on
the job results page — self-hosted on your own domain rather than a
third-party shortener, so a link in a cold text reads as legitimate. This
part is fully optional: without it, the text box just uses the real long
URLs instead of short ones — nothing breaks.

### 1. Create a KV namespace

1. Cloudflare dashboard → **Storage & Databases** → **KV** → **Create namespace**.
2. Name it `pontora-shortlinks` (name doesn't matter functionally, just for
   your own reference in the dashboard).
3. Copy the **Namespace ID** shown after creation.

### 2. Create an API token

1. Same place you made the R2 token — **My Profile** → **API Tokens** →
   **Create Token** (a *user* API token, not the R2-specific one from Part 1;
   KV goes through Cloudflare's regular API, not the S3-compatible one).
2. Use the **Edit Cloudflare Workers** template, or a custom token with
   **Account → Workers KV Storage → Edit** permission.
3. Scope it to your account. Create it and copy the token — same
   one-time-visibility rule as the R2 token.

### 3. Bind the namespace to the existing Worker

1. Workers & Pages → `pontora-generated-sites` (the Worker from Part 1) →
   **Settings** → **Bindings** → **Add binding** → **KV Namespace**.
2. Variable name: `SHORTLINKS` (must match exactly — `worker.js` reads
   `env.SHORTLINKS`). Namespace: the one you created above. Save.
3. Re-deploy the Worker with the updated `cloudflare/generated-sites/worker.js`
   from this repo (it now also handles `/p/<code>` short-link redirects) —
   same Quick Edit or `wrangler deploy` steps as Part 1.

### 4. Set the environment variables

Locally (`.env`) and on Render (service → Environment):

```
CLOUDFLARE_API_TOKEN=<from step 2>
CLOUDFLARE_ACCOUNT_ID=<same account ID as R2, from Part 1 step 2 — reused automatically if already set>
CLOUDFLARE_KV_NAMESPACE_ID=<from step 1>
```

`GENERATED_SITES_BASE_URL` is already set from Part 1 and is reused for
short links too (`{that domain}/p/{code}`).

### Verifying it worked

Generate a site as in Part 1's verification step, then check the log for a
line like:

```
Generated 4 concept(s):
  ...: https://sites.mypontora.com/sites/.../music-studio/index.html
```

then open the job's results page in the webapp — the text-message box
should contain `https://sites.mypontora.com/p/...` links, not the long
`/sites/...` ones. Click one to confirm it redirects to the real page.

If `CLOUDFLARE_KV_NAMESPACE_ID` or the token is missing/wrong, the script
logs a warning per link ("Short link creation failed... using long URL")
and falls back automatically rather than failing the whole generation.
