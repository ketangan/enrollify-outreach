# Google Sheet Schema Reference

Copy this exactly when creating the sheet. Column order matters — the code indexes by column position.

## How to set a dropdown on a column

- Click the column letter to select the whole column
- Menu: **Data → Data validation → + Add rule**
- **Criteria:** Dropdown
- Paste the allowed values (one per row) from the lists below
- **Done**

## How to add conditional formatting

- Select the `status` column
- **Format → Conditional formatting**
- **Format cells if:** Text is exactly → value (e.g. `replied`)
- Set background color → **Done**
- Repeat for each rule below

## Tab 1: `Leads`

Row 1 (headers, frozen):

```
id | name | website | category | city | state | zip | phone | address | discovered_date | status | enrollment_method | owner_name | owner_title | owner_source_url | best_email | email_confidence | last_action | sent_at | follow_up_at | follow_up_sent_at | replied_at | notes | website_mock_candidate | website_mock_type | website_mock_versions | website_mock_status | website_mock_payload | website_mock_generated_at | website_mock_notes
```

**Column types:**
- `id`, `name`, `website`, `city`, `state`, `phone`, `address`, `owner_name`, `owner_title`, `owner_source_url`, `best_email`, `last_action`, `notes` → plain text
- `category` → dropdown (see below)
- `zip` → plain text (preserve leading zeros; format as plain text, not number)
- `discovered_date`, `follow_up_at` → date
- `sent_at`, `follow_up_sent_at`, `replied_at` → datetime (ISO 8601 strings)
- `status`, `enrollment_method`, `email_confidence` → dropdown (see below)
- `website_mock_candidate` → optional `yes/no`; only marked leads can get website-refresh mock links in follow-up emails
- `website_mock_type` → `preschool`, `music`, or `sports`
- `website_mock_versions` → `auto` or comma-separated version ids; `auto` currently creates 2 versions per type
- `website_mock_status` → `not_started`, `generated`, `failed`, or `skip`
- `website_mock_payload` → JSON list of generated mock URLs; supports more versions without adding more columns
- `website_mock_generated_at` → ISO date/time when mock metadata was generated
- `website_mock_notes` → free text for mock workflow decisions

### Dropdown: `category`
```
dance, music, sports, preschool, daycare, martial_arts, art,
gymnastics, swim, tutoring, language, coding_stem, montessori, other
```

### Dropdown: `status`
```
pending_classify, needs_manual_review, online_system_exclude,
ready_for_owner_lookup, ready_for_email_guess, ready_to_send,
awaiting_approval, sent, follow_up_sent, replied,
closed_no_reply, already_contacted, do_not_contact
```

### Dropdown: `enrollment_method`
```
online_system_exclude, email_or_phone_qualify, pdf_form_qualify,
contact_form_qualify, needs_manual_review
```

### Dropdown: `email_confidence`
```
high, medium, low, unverified
```

### Conditional formatting (recommended)
- `status = replied` → bright red background (visual 🚨)
- `status = needs_manual_review` → yellow background
- `status = online_system_exclude` → gray text
- `status = sent` → light green background

## Tab 2: `Already_Contacted`

Row 1:
```
school_name | email | contacted_date | outcome | notes
```

Paste your existing "already emailed" list here. The duplicate-check script matches on strong evidence such as email address, phone, address, exact non-root URL path, or fuzzy school name only when the location also matches.

## Tab 3: `Coverage`

Row 1:
```
zip | city | total_found | qualified | contacted | replied | status | started_date | completed_date
```

### Dropdown: `status`
```
not_started, in_progress, complete
```

Our Phase 1 code writes rows here automatically when a new zip is processed.

## Tab 4: `Templates`

Row 1:
```
template_id | subject | body | last_updated
```

**Initial rows:**

| template_id | subject | body |
|---|---|---|
| contact_form | (Ketan fills in) | (Ketan fills in, with `{{placeholders}}`) |
| email | ... | ... |
| pdf_form | ... | ... |
| follow_up | ... | ... |
| website_mock_followup_addendum | blank | optional P.S. body appended to follow-up drafts for generated website mock candidates |

**Supported placeholders:**
- `{{owner_first_name}}` — e.g. "Jane"
- `{{school_name}}` — e.g. "Lincoln Dance Academy"
- `{{category}}` — e.g. "dance studio"
- `{{specific_observation}}` — 1-2 sentence personalized note drafted by Claude
- `{{website_mock_addendum}}` — optional follow-up-only P.S. inserted when a lead has generated website mock URLs

## Setup checklist

- [ ] Sheet created and named `Pontora Outreach`
- [ ] All 4 tabs exist with exact column headers
- [ ] Dropdowns configured on the relevant columns (Data → Data validation)
- [ ] Conditional formatting rules added
- [ ] Sheet shared with Google service account email as Editor
- [ ] Sheet ID captured in `.env` (`GOOGLE_SHEET_ID`)
- [ ] Ketan's existing "contacted" list pasted into `Already_Contacted`
- [ ] 4 email templates filled in under `Templates` tab
