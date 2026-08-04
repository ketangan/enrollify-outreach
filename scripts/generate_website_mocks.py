#!/usr/bin/env python3
"""
Generate optional website-refresh mock pages for marked leads.

This script is intentionally separate from draft creation. Daily outreach can
run without it. Follow-up drafts only include mock links after this script has
generated URLs and written website_mock_status=generated to the Leads row.

Usage:
  python scripts/generate_website_mocks.py --base-url https://mocks.mypontora.com
  python scripts/generate_website_mocks.py --base-url https://mocks.mypontora.com --write-sheet
  python scripts/generate_website_mocks.py --base-url https://mocks.mypontora.com --force --limit 5
"""

from __future__ import annotations

import argparse
import html
import json
import logging
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gspread.utils import rowcol_to_a1

from src import config, sheets, website_mocks
from src.name_cleaner import clean_school_name

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("website_mocks")

SHEET_WRITE_THROTTLE_SEC = 1.2


def _clean(value) -> str:
    return str(value or "").strip()


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", _clean(value).lower()).strip("-")
    return slug or "school"


def _category_label(category: str) -> str:
    mapping = {
        "preschool": "Preschool",
        "daycare": "Child care",
        "montessori": "Montessori",
        "music": "Music school",
        "dance": "Dance studio",
        "sports": "Sports program",
        "martial_arts": "Martial arts academy",
        "gymnastics": "Gymnastics academy",
        "swim": "Swim school",
        "art": "Art studio",
    }
    return mapping.get(_clean(category).lower(), "School")


def _programs_for(mock_type: str, category: str) -> list[str]:
    if mock_type == "preschool":
        return ["Early learners", "Pre-K readiness", "Flexible enrollment", "Parent updates"]
    if mock_type == "music":
        return ["Private lessons", "Beginner programs", "Performance prep", "Flexible scheduling"]
    if mock_type == "sports":
        if _clean(category).lower() == "martial_arts":
            return ["Kids classes", "Teen training", "Beginner intro", "Progress tracking"]
        return ["Youth classes", "Beginner programs", "Camps and clinics", "Trial sessions"]
    return ["Programs", "Enrollment", "Parent communication", "Reporting"]


def _hero_headline(mock_type: str, variant_id: str, school_name: str) -> str:
    if mock_type == "preschool":
        if variant_id == "structured":
            return f"A clearer first step for {school_name} families."
        return f"A warmer welcome for new {school_name} families."
    if mock_type == "music":
        if variant_id == "performance":
            return f"Lessons, programs, and performances in one clear path."
        return f"Music lessons made easier to explore and book."
    if variant_id == "trust":
        return f"Training families can understand before they ever call."
    return f"Classes, schedules, and signups without the confusion."


def _tracking_script() -> str:
    logger_url = json.dumps(config.CLICK_LOGGER_URL)
    return f"""
<script>
  (function() {{
    const LOGGER_URL = {logger_url};
    const HUMAN_TIMEOUT_MS = 8000;
    const STORAGE_KEY = 'pontora_mock_logged_v1';
    const params = new URLSearchParams(window.location.search);
    const rawLeadId = params.get('utm_content') || params.get('lead') || '';
    const utmSource = params.get('utm_source') || '';
    const utmMedium = params.get('utm_medium') || '';
    const utmCampaign = params.get('utm_campaign') || '';
    const campaignId = [utmSource, utmMedium, utmCampaign].filter(Boolean).join(':');
    const leadId = rawLeadId || (campaignId ? `campaign:${{campaignId}}` : '');
    if (!leadId || !LOGGER_URL) return;
    try {{
      if (sessionStorage.getItem(STORAGE_KEY) === leadId + ':' + location.pathname) return;
    }} catch (e) {{}}
    let logged = false;
    function send(gestureType) {{
      if (logged) return;
      logged = true;
      try {{ sessionStorage.setItem(STORAGE_KEY, leadId + ':' + location.pathname); }} catch (e) {{}}
      const payload = {{
        lead_id: leadId,
        utm_content: rawLeadId,
        utm_source: utmSource,
        utm_medium: utmMedium,
        utm_campaign: utmCampaign,
        tracking_kind: leadId.indexOf('campaign:') === 0 ? 'campaign' : 'lead',
        user_agent: navigator.userAgent || '',
        referer: document.referrer || '',
        path: window.location.pathname,
        gesture_type: gestureType
      }};
      try {{
        fetch(LOGGER_URL, {{
          method: 'POST',
          mode: 'no-cors',
          headers: {{'Content-Type': 'text/plain;charset=utf-8'}},
          body: JSON.stringify(payload),
          keepalive: true
        }}).catch(() => {{}});
      }} catch (e) {{}}
    }}
    const events = ['mousemove', 'scroll', 'keydown', 'touchstart', 'click'];
    function onGesture(e) {{
      send(e.type);
      events.forEach(ev => window.removeEventListener(ev, onGesture, {{passive:true, capture:true}}));
    }}
    events.forEach(ev => window.addEventListener(ev, onGesture, {{passive:true, capture:true, once:false}}));
    setTimeout(() => {{
      events.forEach(ev => window.removeEventListener(ev, onGesture, {{passive:true, capture:true}}));
    }}, HUMAN_TIMEOUT_MS);
  }})();
</script>
"""


def _render_mock_html(lead: dict, variant: website_mocks.MockVariant) -> str:
    school_name = clean_school_name(
        _clean(lead.get("name")),
        city=_clean(lead.get("city")),
        state=_clean(lead.get("state")),
    )
    category = _category_label(_clean(lead.get("category")))
    city = _clean(lead.get("city")) or "your area"
    phone = _clean(lead.get("phone"))
    website = _clean(lead.get("website"))
    programs = _programs_for(variant.type_id, _clean(lead.get("category")))
    headline = _hero_headline(variant.type_id, variant.version_id, school_name)
    accent = html.escape(variant.accent)
    secondary = html.escape(variant.secondary)
    escaped_name = html.escape(school_name)
    escaped_city = html.escape(city)
    escaped_category = html.escape(category)
    escaped_tagline = html.escape(variant.tagline)
    program_cards = "\n".join(
        f"<article><span>{idx:02d}</span><h3>{html.escape(program)}</h3>"
        "<p>Clear details, easy next steps, and a direct inquiry path for families.</p></article>"
        for idx, program in enumerate(programs, start=1)
    )
    contact_line = html.escape(phone) if phone else "Request information"
    website_link = (
        f'<a href="{html.escape(website, quote=True)}" target="_blank" rel="noopener">Current site</a>'
        if website else ""
    )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escaped_name} - Website Concept</title>
  <style>
    :root {{
      --accent: {accent};
      --secondary: {secondary};
      --ink: #071048;
      --muted: #4b587c;
      --paper: #f6fbff;
      --line: #d8e6f3;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--ink);
      background: var(--paper);
    }}
    a {{ color: inherit; }}
    .page {{ min-height: 100vh; }}
    header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 24px clamp(20px, 5vw, 72px);
      background: rgba(255,255,255,.86);
      border-bottom: 1px solid var(--line);
      position: sticky;
      top: 0;
      z-index: 5;
      backdrop-filter: blur(12px);
    }}
    .brand {{
      display: flex;
      gap: 12px;
      align-items: center;
      font-weight: 800;
      letter-spacing: 0;
      font-size: 20px;
    }}
    .brand-mark {{
      width: 38px;
      height: 38px;
      border-radius: 12px;
      background: linear-gradient(135deg, var(--accent), #0f5db8 60%, var(--secondary));
      display: grid;
      place-items: center;
      color: white;
      font-weight: 900;
    }}
    nav {{ display: flex; gap: 24px; align-items: center; color: var(--muted); font-weight: 650; }}
    .nav-cta {{
      color: white;
      text-decoration: none;
      background: var(--secondary);
      padding: 12px 18px;
      border-radius: 8px;
    }}
    .hero {{
      display: grid;
      grid-template-columns: minmax(0, 1.08fr) minmax(340px, .92fr);
      gap: clamp(28px, 5vw, 74px);
      padding: clamp(56px, 8vw, 120px) clamp(20px, 5vw, 72px) 42px;
      align-items: center;
    }}
    .eyebrow {{
      display: inline-flex;
      align-items: center;
      gap: 10px;
      padding: 8px 14px;
      border: 1px solid var(--line);
      border-radius: 999px;
      color: var(--muted);
      background: white;
      font-weight: 700;
      margin-bottom: 22px;
    }}
    .eyebrow::before {{
      content: "";
      width: 10px;
      height: 10px;
      border-radius: 999px;
      background: var(--accent);
    }}
    h1 {{
      font-size: clamp(46px, 7vw, 96px);
      line-height: .94;
      letter-spacing: 0;
      margin: 0 0 24px;
      max-width: 960px;
    }}
    .lead {{
      font-size: clamp(19px, 2vw, 26px);
      line-height: 1.55;
      color: var(--muted);
      max-width: 780px;
      margin: 0 0 34px;
    }}
    .hero-actions {{ display: flex; flex-wrap: wrap; gap: 14px; align-items: center; }}
    .primary {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      text-decoration: none;
      background: var(--secondary);
      color: white;
      padding: 16px 22px;
      border-radius: 8px;
      font-weight: 800;
    }}
    .secondary-link {{
      color: var(--secondary);
      font-weight: 800;
      text-decoration: none;
      border-bottom: 2px solid var(--accent);
      padding-bottom: 4px;
    }}
    .panel {{
      background: white;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 26px;
      box-shadow: 0 24px 60px rgba(15, 40, 80, .12);
    }}
    .panel h2 {{ margin: 0 0 18px; font-size: 28px; }}
    .steps {{ display: grid; gap: 14px; }}
    .step {{
      display: grid;
      grid-template-columns: 36px 1fr;
      gap: 14px;
      align-items: start;
      padding: 16px;
      background: #f8fbff;
      border: 1px solid var(--line);
      border-radius: 8px;
    }}
    .step strong {{
      width: 36px;
      height: 36px;
      display: grid;
      place-items: center;
      border-radius: 999px;
      background: var(--accent);
      color: white;
    }}
    .programs {{
      padding: 38px clamp(20px, 5vw, 72px) 76px;
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 16px;
    }}
    article {{
      background: white;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 22px;
      min-height: 210px;
    }}
    article span {{
      color: var(--accent);
      font-weight: 900;
      font-size: 13px;
    }}
    article h3 {{ margin: 18px 0 10px; font-size: 24px; }}
    article p {{ margin: 0; color: var(--muted); line-height: 1.55; }}
    .footer-band {{
      padding: 34px clamp(20px, 5vw, 72px);
      background: var(--secondary);
      color: white;
      display: flex;
      justify-content: space-between;
      gap: 20px;
      align-items: center;
      flex-wrap: wrap;
    }}
    .footer-band p {{ margin: 0; color: rgba(255,255,255,.78); }}
    .concept-note {{
      font-size: 13px;
      color: #667085;
      padding: 18px clamp(20px, 5vw, 72px);
      background: white;
      border-top: 1px solid var(--line);
    }}
    @media (max-width: 860px) {{
      header, nav {{ align-items: flex-start; }}
      nav {{ display: none; }}
      .hero {{ grid-template-columns: 1fr; }}
      .programs {{ grid-template-columns: 1fr 1fr; }}
    }}
    @media (max-width: 560px) {{
      .programs {{ grid-template-columns: 1fr; }}
      .hero-actions {{ align-items: stretch; }}
      .primary {{ justify-content: center; }}
    }}
  </style>
</head>
<body>
  <div class="page">
    <header>
      <div class="brand"><span class="brand-mark">{escaped_name[:1] or "P"}</span>{escaped_name}</div>
      <nav>
        <a href="#programs">Programs</a>
        <a href="#about">About</a>
        <a href="#contact" class="nav-cta">Start enrollment</a>
      </nav>
    </header>
    <main>
      <section class="hero">
        <div>
          <div class="eyebrow">{escaped_category} in {escaped_city}</div>
          <h1>{html.escape(headline)}</h1>
          <p class="lead">{escaped_tagline} Families can understand the programs, ask questions, and begin enrollment without digging through old pages or PDFs.</p>
          <div class="hero-actions">
            <a class="primary" href="#contact">Start enrollment -></a>
            <a class="secondary-link" href="#programs">Explore programs</a>
          </div>
        </div>
        <aside class="panel" id="contact">
          <h2>Inquiry snapshot</h2>
          <div class="steps">
            <div class="step"><strong>1</strong><div><b>Choose a program</b><br>Parents see clear options before reaching out.</div></div>
            <div class="step"><strong>2</strong><div><b>Send the details</b><br>Contact info, student info, and questions land in one place.</div></div>
            <div class="step"><strong>3</strong><div><b>Follow up fast</b><br>The school can reply, approve, waitlist, or invite the family in.</div></div>
          </div>
        </aside>
      </section>
      <section class="programs" id="programs">
        {program_cards}
      </section>
      <section class="footer-band" id="about">
        <div>
          <h2>Built around the first parent impression.</h2>
          <p>{escaped_name} can keep its current process while giving families a cleaner way to take the next step.</p>
        </div>
        <div>
          <strong>{contact_line}</strong>
          {website_link}
        </div>
      </section>
    </main>
    <div class="concept-note">
      Concept mock generated by Pontora for outreach preview. This is not the live website for {escaped_name}.
    </div>
  </div>
  {_tracking_script()}
</body>
</html>
"""


def _candidate_rows(rows: list[dict], force: bool) -> list[dict]:
    candidates = []
    for row in rows:
        if not website_mocks.is_mock_candidate(row):
            continue
        if _clean(row.get("status")) in {
            "do_not_contact",
            "bounced",
            "closed_no_reply",
            "online_system_exclude",
            "already_contacted",
        }:
            continue
        mock_status = _clean(row.get("website_mock_status")).lower()
        if not force and mock_status == "generated":
            continue
        if mock_status == "skip":
            continue
        candidates.append(row)
    return candidates


def _render_candidate(lead: dict, output_dir: Path, base_url: str) -> list[dict]:
    payload = website_mocks.build_payload(lead, base_url)
    if not payload:
        return []

    lead_id = _clean(lead.get("id"))
    mock_type = website_mocks.normalize_mock_type(
        _clean(lead.get("website_mock_type")),
        category=_clean(lead.get("category")),
    )
    variants = {
        f"{variant.type_id}-{variant.version_id}": variant
        for variant in website_mocks.variants_for(mock_type, _clean(lead.get("website_mock_versions")))
    }

    for item in payload:
        variant_key = f"{item['type']}-{item['version']}"
        variant = variants.get(variant_key)
        if not variant:
            continue
        page_dir = output_dir / "mocks" / _slug(lead_id) / variant_key
        page_dir.mkdir(parents=True, exist_ok=True)
        (page_dir / "index.html").write_text(_render_mock_html(lead, variant), encoding="utf-8")
    return payload


def _write_index(output_dir: Path, rendered: list[dict]) -> None:
    rows = "\n".join(
        f"<li><a href=\"{html.escape(item['url'], quote=True)}\">"
        f"{html.escape(item['school'])} - {html.escape(item['label'])}</a></li>"
        for item in rendered
    )
    rows = rows or "<li>No mock pages generated.</li>"
    (output_dir / "index.html").write_text(
        "<!doctype html><html><head><meta charset=\"utf-8\"><meta name=\"viewport\" "
        "content=\"width=device-width, initial-scale=1\"><title>Pontora website mocks</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:900px;margin:48px auto;"
        "padding:0 20px;color:#071048}li{margin:10px 0}</style></head><body>"
        "<h1>Pontora website mocks</h1><ul>"
        f"{rows}</ul></body></html>",
        encoding="utf-8",
    )


def _update_sheet_rows(ws, all_rows: list[list[str]], updates_by_id: dict[str, dict]) -> None:
    headers = all_rows[0]
    id_col = headers.index("id")
    for row_idx, row in enumerate(all_rows[1:], start=2):
        if len(row) <= id_col:
            continue
        lead_id = row[id_col].strip()
        updates = updates_by_id.get(lead_id)
        if not updates:
            continue
        batch = [
            {"range": rowcol_to_a1(row_idx, headers.index(key) + 1), "values": [[value]]}
            for key, value in updates.items()
            if key in headers
        ]
        if not batch:
            continue
        ws.batch_update(batch, value_input_option="USER_ENTERED")
        time.sleep(SHEET_WRITE_THROTTLE_SEC)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=config.WEBSITE_MOCK_BASE_URL,
                        help="Public base URL, e.g. https://mocks.mypontora.com")
    parser.add_argument("--output-dir", default="generated/website-mocks-site")
    parser.add_argument("--write-sheet", action="store_true",
                        help="Write generated mock payload/status back to Leads")
    parser.add_argument("--force", action="store_true",
                        help="Regenerate rows already marked generated")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    config.validate()
    base_url = _clean(args.base_url)
    if not base_url:
        logger.info("WEBSITE_MOCK_BASE_URL/base-url is empty; nothing to generate.")
        return

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    ws = sheets.get_tab(config.TAB_LEADS)
    if args.write_sheet:
        sheets.ensure_headers(config.TAB_LEADS, website_mocks.MOCK_LEAD_HEADERS)
    all_rows = ws.get_all_values()
    headers = all_rows[0] if all_rows else []
    rows = [
        {header: row[idx] if idx < len(row) else "" for idx, header in enumerate(headers)}
        for row in all_rows[1:]
    ]

    candidates = _candidate_rows(rows, force=args.force)
    if args.limit and args.limit > 0:
        candidates = candidates[:args.limit]

    logger.info("Website mock candidates to render: %d", len(candidates))

    rendered_for_index = []
    updates_by_id: dict[str, dict] = {}
    now = datetime.now(ZoneInfo(config.TIMEZONE)).replace(microsecond=0)

    for lead in candidates:
        lead_id = _clean(lead.get("id"))
        school_name = clean_school_name(
            _clean(lead.get("name")),
            city=_clean(lead.get("city")),
            state=_clean(lead.get("state")),
        )
        payload = _render_candidate(lead, output_dir, base_url)
        if not payload:
            logger.warning("Skipping %s: could not build mock payload", lead_id)
            continue

        logger.info("Rendered %s (%s): %d version(s)", school_name, lead_id, len(payload))
        for item in payload:
            rendered_for_index.append({
                "school": school_name,
                "label": item["label"],
                "url": item["url"],
            })

        existing_notes = _clean(lead.get("website_mock_notes"))
        note = (
            f"Generated website mocks on {now.date().isoformat()}; "
            f"versions={','.join(item['type'] + '-' + item['version'] for item in payload)}."
        )
        updates_by_id[lead_id] = {
            "website_mock_type": website_mocks.normalize_mock_type(
                _clean(lead.get("website_mock_type")),
                category=_clean(lead.get("category")),
            ),
            "website_mock_versions": _clean(lead.get("website_mock_versions")) or "auto",
            "website_mock_status": "generated",
            "website_mock_payload": json.dumps(payload, separators=(",", ":")),
            "website_mock_generated_at": now.isoformat(),
            "website_mock_notes": website_mocks.append_note(existing_notes, note),
            "last_action": "website_mock_generated",
        }

    _write_index(output_dir, rendered_for_index)

    if args.write_sheet and updates_by_id:
        logger.info("Writing generated mock metadata to Leads: %d row(s)", len(updates_by_id))
        updated_rows = ws.get_all_values()
        _update_sheet_rows(ws, updated_rows, updates_by_id)
    elif args.write_sheet:
        logger.info("No sheet updates needed.")


if __name__ == "__main__":
    main()
