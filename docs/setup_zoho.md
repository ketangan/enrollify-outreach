# Zoho Mail Reference

Main steps are in `PHASE_0_CHECKLIST.md`. This file has debugging info.

## What you're setting up

- Business email at `ketan@enrollifyapp.com`
- Free forever (5 users, 5GB/user on custom domain)
- Proper SPF/DKIM/DMARC (good deliverability)
- IMAP/SMTP access (scripts can read/write drafts)

## Send limits (free tier)

- **25 emails/day** to external domains (matches our daily cap by design)
- **250MB/hour** inbound — not a concern
- Hit the limit? Sends after #26 fail silently until midnight UTC

## Server settings (if something prompts you)

| | Host | Port | Security |
|---|---|---|---|
| IMAP (incoming) | `imap.zoho.com` | 993 | SSL |
| SMTP (outgoing) | `smtp.zoho.com` | 465 | SSL |

## Troubleshooting commands

Run these in Terminal if something isn't working.

**Check MX records:**
```
dig MX enrollifyapp.com
```
Should return `mx.zoho.com`, `mx2.zoho.com`, `mx3.zoho.com`.

**Check SPF:**
```
dig TXT enrollifyapp.com | grep spf
```
Should show `v=spf1 include:zoho.com ~all`.

**Check DKIM:**
```
dig TXT zoho._domainkey.enrollifyapp.com
```
Should show a long `v=DKIM1; k=rsa; p=...` string.

**Check DMARC:**
```
dig TXT _dmarc.enrollifyapp.com
```
Should show `v=DMARC1; p=none; ...`.

## Common gotchas

- **DNS propagation** takes 2-30 minutes on Cloudflare. Usually fast, occasionally slow. If `dig` shows nothing 30 minutes after adding a record, double-check you saved it in Cloudflare.
- **Zoho upsells constantly.** Keep clicking "Free," "Skip," "Continue with Free."
- **Two accounts exist:** admin (your personal Gmail) and mailbox (ketan@enrollifyapp.com). App password must be generated from the *mailbox* account at `accounts.zoho.com`, not the admin.
- **If Zoho Mail Lite / free tier options disappear** during signup, the plan names may have changed. Look for anything with "Forever Free" or that explicitly says $0/mo. If only paid options appear, Zoho may have removed the free tier in your region — fall back to paid Zoho ($1/user/mo) or Google Workspace ($7/mo).

## After 1 week of sending

Upgrade DMARC from `p=none` to `p=quarantine`:
- [ ] Cloudflare DNS → edit `_dmarc` TXT record
- [ ] Change `p=none` to `p=quarantine`
- [ ] Save

Don't jump to `p=reject` unless you're 100% sure SPF/DKIM are working — it will bounce legit mail.
