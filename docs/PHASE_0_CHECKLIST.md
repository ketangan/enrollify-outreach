# Phase 0 — Setup Checklist

Work through in order. Don't skip. Estimated time: 2-3 hours.

---

## 1. Get the code into a repo

- [ ] Unzip `enrollify-outreach-phase0.zip` into `~/code/enrollify-outreach`
- [ ] Open Terminal
- [ ] `cd ~/code/enrollify-outreach`
- [ ] `git init`
- [ ] `git add .`
- [ ] `git commit -m "Phase 0: project scaffolding"`
- [ ] Go to https://github.com/new
- [ ] Repository name: `enrollify-outreach`
- [ ] Set to **Private**
- [ ] Click **Create repository**
- [ ] Back in Terminal, copy-paste the commands GitHub shows under "push an existing repository" (will look like):
  ```
  git remote add origin https://github.com/YOUR_USERNAME/enrollify-outreach.git
  git branch -M main
  git push -u origin main
  ```

---

## 2. Set up Python

- [ ] `python3 --version` — must show 3.11 or higher. If not, install from https://www.python.org/downloads/
- [ ] `cd ~/code/enrollify-outreach`
- [ ] `python3 -m venv venv`
- [ ] `source venv/bin/activate`
- [ ] `pip install -r requirements.txt`
- [ ] `cp .env.example .env`

You'll fill in `.env` values as you complete the steps below.

---

## 3. Anthropic API key

Walkthrough: [`setup_anthropic.md`](./setup_anthropic.md)

- [ ] Go to https://console.anthropic.com/
- [ ] Log in
- [ ] **Plans & Billing → Add credits → $10**
- [ ] **Settings → API Keys → Create Key** → name it `enrollify-outreach`
- [ ] Copy the key
- [ ] Open `.env` in a text editor
- [ ] Paste after `ANTHROPIC_API_KEY=`
- [ ] Save

---

## 4. Google Cloud — Places API key

Walkthrough: [`setup_google.md`](./setup_google.md)

- [ ] Go to https://console.cloud.google.com/
- [ ] Top-left project selector → **New Project** → name: `enrollify-outreach` → **Create**
- [ ] Switch to the new project (top-left selector)
- [ ] Left menu → **Billing** → link or add a billing account
- [ ] **Billing → Budgets & alerts → Create budget** → $5/month → alerts at 50%, 90%, 100%
- [ ] Left menu → **APIs & Services → Library**
- [ ] Search and enable: **Places API (New)**
- [ ] Search and enable: **Google Sheets API**
- [ ] Search and enable: **Google Drive API**
- [ ] **APIs & Services → Credentials → + Create Credentials → API key**
- [ ] Copy the key
- [ ] Paste into `.env` after `GOOGLE_PLACES_API_KEY=`
- [ ] Click **Restrict Key** on the new key
- [ ] Under **API restrictions** → select **Restrict key** → check only **Places API (New)**
- [ ] Click **Save**

---

## 5. Google Cloud — Service account for Sheets

- [ ] **APIs & Services → Credentials → + Create Credentials → Service account**
- [ ] Name: `enrollify-sheets-writer` → **Create and Continue** → **Continue** (skip roles) → **Done**
- [ ] Click the new service account → **Keys tab → Add Key → Create new key → JSON → Create**
- [ ] A JSON file downloads to your Downloads folder
- [ ] In Terminal: `mv ~/Downloads/enrollify-outreach-*.json ~/code/enrollify-outreach/config/google-service-account.json`
- [ ] Open that JSON file in a text editor
- [ ] Find the `"client_email"` line — copy that email address
- [ ] Save it somewhere — you need it in the next step

---

## 6. Create the Google Sheet

Walkthrough: [`sheet_schema.md`](./sheet_schema.md) has exact column headers.

- [ ] Go to https://sheets.google.com
- [ ] Click **Blank spreadsheet**
- [ ] Rename to `Enrollify Outreach`
- [ ] Create 4 tabs: `Leads`, `Already_Contacted`, `Coverage`, `Templates` (rename tab 1, right-click tab to add more)
- [ ] For each tab, paste the column headers from `sheet_schema.md` into row 1
- [ ] Select row 1 → **View → Freeze → 1 row**
- [ ] Configure dropdowns (instructions in `sheet_schema.md`)
- [ ] Add conditional formatting (instructions in `sheet_schema.md`)
- [ ] Click **Share** (top right) → paste the service account email from step 5 → set to **Editor** → uncheck "Notify people" → **Share**
- [ ] Copy the sheet ID from the URL — it's the long string between `/d/` and `/edit`
- [ ] Paste into `.env` after `GOOGLE_SHEET_ID=`
- [ ] Paste your existing "already emailed" list into the `Already_Contacted` tab

---

## 7. Zoho Mail — create account

Walkthrough: [`setup_zoho.md`](./setup_zoho.md)

- [ ] Go to https://www.zoho.com/mail/
- [ ] Click **Sign up for free** → choose **Forever Free Plan**
- [ ] Enter domain: `enrollifyapp.com`
- [ ] Create admin account (use your personal Gmail for admin login — NOT a new Zoho address)
- [ ] When Zoho shows the TXT verification code, keep that browser tab open

---

## 8. Zoho — verify domain via Cloudflare

- [ ] Copy the TXT value Zoho shows (starts with `zoho-verification=`)
- [ ] New tab → log in to Cloudflare → click `enrollifyapp.com`
- [ ] Left menu → **DNS → Records → + Add record**
- [ ] Type: `TXT` · Name: `@` · Content: paste the Zoho TXT value · Proxy: **DNS only** (gray cloud) · TTL: Auto
- [ ] **Save**
- [ ] Wait 2 minutes
- [ ] Back in Zoho tab → click **Verify**

---

## 9. Zoho — create mailbox + MX records

- [ ] In Zoho: **Add/Create user** → username: `ketan` → set password → **Create**
- [ ] In Cloudflare DNS, delete any existing MX records
- [ ] Add MX record: Name `@` · Mail server `mx.zoho.com` · Priority `10`
- [ ] Add MX record: Name `@` · Mail server `mx2.zoho.com` · Priority `20`
- [ ] Add MX record: Name `@` · Mail server `mx3.zoho.com` · Priority `50`

---

## 10. Zoho — SPF, DKIM, DMARC records

**SPF:**
- [ ] In Cloudflare DNS → **+ Add record**
- [ ] Type: `TXT` · Name: `@` · Content: `v=spf1 include:zoho.com ~all` · TTL: Auto → **Save**

**DKIM:**
- [ ] In Zoho: **Admin Console → Domains → enrollifyapp.com → Email Configuration → DKIM**
- [ ] Click **Add** → Selector: `zoho` → **Generate**
- [ ] Keep the tab open — Zoho shows a Name and Value
- [ ] In Cloudflare DNS → **+ Add record**
- [ ] Type: `TXT` · Name: `zoho._domainkey` · Content: paste the long `v=DKIM1; k=rsa; p=...` value · TTL: Auto → **Save**
- [ ] Wait 5 minutes
- [ ] Back in Zoho DKIM page → click **Verify**

**DMARC:**
- [ ] In Cloudflare DNS → **+ Add record**
- [ ] Type: `TXT` · Name: `_dmarc` · Content: `v=DMARC1; p=none; rua=mailto:ketan@enrollifyapp.com` · TTL: Auto → **Save**

---

## 11. Zoho — enable IMAP + create app password

- [ ] Log in at https://mail.zoho.com as `ketan@enrollifyapp.com`
- [ ] Gear icon (top right) → **Settings → Mail Accounts → IMAP Access**
- [ ] Toggle **IMAP** on → **Save**
- [ ] New tab → https://accounts.zoho.com/home#security/app_password
- [ ] Log in as `ketan@enrollifyapp.com` (not the admin account)
- [ ] Click **Generate New Password**
- [ ] Name: `enrollify-outreach-scripts` → **Generate**
- [ ] Copy the 16-character password
- [ ] Paste into `.env` after `ZOHO_APP_PASSWORD=`
- [ ] Save `.env`

---

## 12. Test Zoho actually sends

- [ ] In Zoho web mail: compose → send a test email to your personal Gmail
- [ ] Open the email in Gmail → click the three dots → **Show original**
- [ ] Look for: `SPF: PASS`, `DKIM: PASS`, `DMARC: PASS`
- [ ] If any fail: DNS hasn't propagated yet — wait 30 min, retry

---

## 13. Write your 4 email templates

- [ ] Open the Google Sheet → `Templates` tab
- [ ] Row 2: `template_id` = `contact_form` — fill in `subject` and `body` using your existing email
- [ ] Row 3: `template_id` = `email`
- [ ] Row 4: `template_id` = `pdf_form`
- [ ] Row 5: `template_id` = `follow_up`
- [ ] Use placeholders: `{{owner_first_name}}`, `{{school_name}}`, `{{category}}`, `{{specific_observation}}`

---

## 14. Final sanity check

- [ ] `cd ~/code/enrollify-outreach && source venv/bin/activate`
- [ ] `python -c "import anthropic, gspread, googlemaps; print('OK')"` → should print `OK`
- [ ] `git status` → `.env` and `config/google-service-account.json` should NOT appear
- [ ] `cat .env | grep -c "="` → should show 15+ lines filled in

---

## Done?

- [ ] Start a new chat with Claude
- [ ] Paste the full contents of `PROJECT.md` as your first message
- [ ] Add: **"Phase 0 complete. Ready for Phase 1."**
