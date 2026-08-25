#!/usr/bin/env python3
"""
Generate a full (not mock/placeholder) business website from real content.

Pulls from whatever sources are available — Google Places reviews and
photos, pasted Yelp review text, an informational page, and/or the
business's own website — merges them, and fills real content gaps with a
conservative, fact-free LLM guess (src/mock_content_llm.py) instead of
generic canned copy when there isn't enough real signal to work with.

This is the reusable core behind the "generate a full site" webapp page
(webapp/webapp/routes_site_generator.py) — also testable standalone via the
CLI below. The actual rendering goes through the same
render_mock_concepts()/write_mock_files() functions the mock-refresh
pipeline uses (see scripts/generate_website_mocks.py) — no separate
rendering path to maintain.

Storage: generated files go to Cloudflare R2 when configured
(R2_ACCOUNT_ID/R2_ACCESS_KEY_ID/R2_SECRET_ACCESS_KEY/GENERATED_SITES_BASE_URL
— see docs/setup_site_generator.md), which is durable across the webapp
process restarting. Falls back to local disk when R2 isn't configured,
which is fine for local testing but NOT durable on a host that recycles its
filesystem (e.g. Render free tier) — see the setup doc before relying on
this in production.

Usage:
  python scripts/generate_full_site.py \
      --name "Riverside Music Collective" --category music \
      --city Austin --state TX --phone "(512) 555-0100" \
      --info-pages https://example.com/about \
      --output-dir generated/full-sites --record-to-sheet

  python scripts/generate_full_site.py \
      --name "Magic Roots Preschool" --category preschool --city "Los Angeles" \
      --no-google-reviews --yelp-text-file yelp_reviews.txt \
      --output-dir generated/full-sites
"""

from __future__ import annotations

import argparse
import html
import json
import logging
import re
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from anthropic import Anthropic

from scripts import generate_website_mocks as mocks
from src import config, mock_content_llm, places, r2_storage, site_generator_state, website_mocks

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("generate_full_site")


def _persist_photos(place: places.DiscoveredPlace | None, subject_id: str, output_dir: Path) -> list[str]:
    """Fetch real business photos server-side and persist them — to R2 when
    configured (durable), else local disk next to the generated pages
    (fine locally, not durable on Render — see module docstring). Returns
    URLs for use as a _website_mock_photos override, or [] on any failure —
    the renderer falls back to stock photos automatically when this is empty."""
    if not place or not place.google_photo_names:
        return []

    use_r2 = r2_storage.is_configured()
    photos_dir = output_dir / "mocks" / subject_id / "photos"
    if not use_r2:
        photos_dir.mkdir(parents=True, exist_ok=True)

    urls = []
    for idx, photo_name in enumerate(place.google_photo_names):
        result = places.fetch_photo_bytes(photo_name)
        if not result:
            continue
        photo_bytes, content_type = result
        ext = "png" if "png" in content_type else "jpg"
        if use_r2:
            key = f"sites/{subject_id}/photos/{idx}.{ext}"
            r2_storage.upload_bytes(key, photo_bytes, content_type)
            urls.append(r2_storage.public_url(key))
        else:
            (photos_dir / f"{idx}.{ext}").write_bytes(photo_bytes)
            urls.append(f"../photos/{idx}.{ext}")
    return urls


def _preview_shell_html(site_name: str) -> str:
    """A thin wrapper page prospects land on: opens to a Desktop view of the
    real site by default, with Desktop/Tablet/Phone buttons to switch. The
    real site loads in an iframe rather than the wrapper resizing a plain
    container, because the site's responsive layout is driven by real CSS
    `@media` breakpoints, which respond to viewport width — an iframe gets
    its own independent viewport, so setting *its* width to 820px/390px
    correctly triggers the tablet/phone CSS. Shrinking a container div would
    not: the desktop layout would just get squished, not actually switch."""
    title = html.escape(f"{site_name} - Website Preview") if site_name else "Website Preview"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; height: 100%; background: #f4f2ee; font: 14px/1.4 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
  .toolbar {{
    display: flex; align-items: center; justify-content: center; gap: 8px;
    padding: 12px 16px; background: #fff; border-bottom: 1px solid #e4e1da;
  }}
  .toolbar button {{
    border: 1px solid #e4e1da; background: #fff; border-radius: 999px;
    padding: 7px 16px; font: inherit; font-weight: 600; color: #55524a; cursor: pointer;
  }}
  .toolbar button[aria-pressed="true"] {{ background: #1c1a16; color: #fff; border-color: #1c1a16; }}
  .stage {{ height: calc(100% - 49px); display: flex; justify-content: center; padding: 20px; overflow: auto; }}
  iframe {{ width: 100%; max-width: 100%; height: 100%; border: 0; background: #fff; transition: width .2s ease; box-shadow: 0 12px 40px rgba(0,0,0,.08); }}
</style>
</head>
<body>
  <div class="toolbar" role="group" aria-label="Preview size">
    <button type="button" data-w="100%" aria-pressed="true">Desktop</button>
    <button type="button" data-w="820px" aria-pressed="false">Tablet</button>
    <button type="button" data-w="390px" aria-pressed="false">Phone</button>
  </div>
  <div class="stage"><iframe src="site.html" title="{title}"></iframe></div>
  <script>
    document.querySelectorAll('.toolbar button').forEach(function (btn) {{
      btn.addEventListener('click', function () {{
        document.querySelectorAll('.toolbar button').forEach(function (b) {{ b.setAttribute('aria-pressed', 'false'); }});
        btn.setAttribute('aria-pressed', 'true');
        document.querySelector('iframe').style.width = btn.dataset.w;
      }});
    }});
  </script>
</body>
</html>
"""


def _persist_rendered(rendered: list[dict], subject_id: str, output_dir: Path, site_name: str = "") -> list[dict]:
    """Write rendered HTML to R2 when configured (durable, and given a
    clean /sites/ URL instead of whatever render_mock_concepts computed via
    the shared /mocks/ path convention), else local disk via
    write_mock_files (fine locally, not durable on Render).

    On R2, each concept becomes two objects: the real rendered page at
    site.html, and a Desktop/Tablet/Phone preview shell at index.html that
    loads it. The public URL still points at index.html, so prospects land
    on the shell (Desktop by default) and everything already linking to
    that URL (webapp UI, Sheet records, outreach emails) needs no changes.
    Local-disk fallback (dev/testing only, not durable, not what a real
    prospect sees) skips the shell and stays plain content, same as before."""
    if r2_storage.is_configured():
        persisted = []
        shell_html = _preview_shell_html(site_name)
        for item in rendered:
            base_key = f"sites/{subject_id}/{item['type']}-{item['version']}"
            r2_storage.upload_bytes(f"{base_key}/site.html", item["html"].encode("utf-8"), "text/html; charset=utf-8")
            r2_storage.upload_bytes(f"{base_key}/index.html", shell_html.encode("utf-8"), "text/html; charset=utf-8")
            public_url = r2_storage.public_url(f"{base_key}/index.html")
            persisted.append({**item, "url": public_url, "preview_url": public_url})
        return persisted

    mocks.write_mock_files(output_dir, subject_id, rendered)
    return rendered


def generate_full_site(
    *,
    name: str,
    category: str,
    city: str = "",
    state: str = "",
    address: str = "",
    phone: str = "",
    website: str = "",
    info_page_urls: str = "",
    yelp_review_text: str = "",
    use_google_places: bool = True,
    versions: str = "auto",
    revision_notes: str = "",
    subject_id: str = "",
    base_url: str,
    output_dir: Path,
    anthropic_client: Anthropic | None = None,
) -> list[dict]:
    """The full pipeline: gather real content from whatever sources are
    given (all optional except name/category), fill sparse gaps
    conservatively (or steer them with revision_notes — see
    infer_program_labels/infer_theme_colors), render the requested
    concept(s), persist them (R2 if configured, else local disk). Returns
    render_mock_concepts()-shaped output with "html" stripped and
    "subject_id" added.

    `info_page_urls` accepts one or more URLs, comma or pipe separated —
    each is fetched independently and its signal merged in.

    Pass `versions` scoped to one theme (e.g. "warm") and reuse the same
    `subject_id` from a prior call to regenerate a single existing concept
    rather than generate a fresh business from scratch."""
    mock_type = website_mocks.normalize_mock_type("", category=category)
    subject_id = subject_id or f"{mocks._slug(name)}-{uuid.uuid4().hex[:6]}"
    subject = {
        "id": subject_id,
        "name": name,
        "category": category,
        "city": city,
        "state": state,
        "phone": phone,
        "website": website,
    }

    signals = []
    review_raw_texts = []
    place = None

    if use_google_places:
        try:
            place = places.find_business(name, city, state, address=address)
        except places.PlacesAuthError as e:
            logger.warning("Google Places lookup unavailable, continuing without it: %s", e)
            place = None
        if place:
            # Only fill gaps the caller didn't already supply — never
            # overwrite a human-provided phone/website with a guess.
            subject["phone"] = subject["phone"] or place.phone
            subject["website"] = subject["website"] or place.website
            if place.google_reviews:
                review_text = " ".join(r.get("text", "") for r in place.google_reviews)
                signals.append(mocks.content_signal_from_reviews(
                    place.google_reviews, mock_type=mock_type, category=category, school_name=name,
                ))
                review_raw_texts.append(review_text)

    if yelp_review_text.strip():
        signals.append(mocks.content_signal_from_reviews(
            yelp_review_text, mock_type=mock_type, category=category, school_name=name,
        ))
        review_raw_texts.append(yelp_review_text)

    for page_url in re.split(r"[,|]", info_page_urls or ""):
        page_url = page_url.strip()
        if page_url:
            signals.append(mocks.content_signal_from_website(
                page_url, mock_type=mock_type, category=category, school_name=name,
            ))

    if website.strip():
        signals.append(mocks.content_signal_from_website(
            website, mock_type=mock_type, category=category, school_name=name,
        ))

    merged = mocks.merge_content_signals(signals)
    revision_notes = (revision_notes or "").strip()

    # Regex extraction found little/nothing usable — ask the LLM for
    # plausible labels grounded in whatever review text we have, rather than
    # falling all the way back to fully generic copy. Also run this whenever
    # the caller gave revision_notes, even if labels already look sufficient
    # — a "regenerate with this change" request should always get a chance
    # to actually take effect. Never touches the quote (must stay
    # real-extracted-text-only, see mock_content_llm.py).
    if (len(merged["labels"]) < 2 or revision_notes) and (review_raw_texts or revision_notes):
        inferred = mock_content_llm.infer_program_labels(
            name=name,
            mock_type=mock_type,
            category=category,
            known_labels=merged["labels"],
            raw_signal_text=" ".join(review_raw_texts),
            revision_notes=revision_notes,
            client=anthropic_client,
        )
        if inferred:
            # A revision request's fresh labels should win over stale ones
            # from the prior generation, not just get appended after them.
            priority = [{"labels": inferred, "quote": ""}, merged] if revision_notes else [merged, {"labels": inferred, "quote": ""}]
            merged = mocks.merge_content_signals(priority)

    if revision_notes:
        color_override = mock_content_llm.infer_theme_colors(
            revision_notes=revision_notes, category=category, client=anthropic_client,
        )
        if color_override:
            subject["_website_mock_color_override"] = color_override

    photo_urls = _persist_photos(place, subject_id, output_dir)
    if photo_urls:
        subject["_website_mock_photos"] = photo_urls

    rendered = mocks.render_mock_concepts(
        subject, base_url=base_url, mock_type=mock_type, versions=versions, content_signal=merged,
    )
    if not rendered:
        return []
    persisted = _persist_rendered(rendered, subject_id, output_dir, site_name=name)
    return [{**{k: v for k, v in item.items() if k != "html"}, "subject_id": subject_id} for item in persisted]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--category", required=True, help="e.g. preschool, music, martial_arts, dance, swim")
    parser.add_argument("--city", default="")
    parser.add_argument("--state", default="")
    parser.add_argument("--address", default="", help="Street address — helps Places match the right business for common names")
    parser.add_argument("--phone", default="")
    parser.add_argument("--website", default="", help="The business's own current website, if any")
    parser.add_argument("--info-pages", default="", help="Comma-separated informational page URL(s) to pull content from")
    parser.add_argument("--yelp-text-file", default="", help="Path to a text file with pasted Yelp review text")
    parser.add_argument("--yelp-text", default="", help="Pasted Yelp review text directly (alternative to --yelp-text-file)")
    parser.add_argument("--google-reviews", dest="use_google", action="store_true", default=True)
    parser.add_argument("--no-google-reviews", dest="use_google", action="store_false")
    parser.add_argument("--versions", default="auto", help="'auto' for all 4, or a single theme id (e.g. 'warm') to regenerate just one")
    parser.add_argument("--revision-notes", default="", help="Optional freeform request for what should change on a regeneration")
    parser.add_argument("--subject-id", default="", help="Reuse an existing subject id to regenerate that same business")
    parser.add_argument("--base-url", default=config.WEBSITE_MOCK_BASE_URL)
    parser.add_argument("--output-dir", default="generated/full-sites")
    parser.add_argument("--record-to-sheet", action="store_true",
                         help="Write this generation into the Generated_Sites sheet tab (the webapp always passes this)")
    parser.add_argument("--org-id", default="", help="Stable org id to record a regeneration under (omit for a first-time generation)")
    parser.add_argument("--theme", default="", help="Theme id (e.g. 'preschool-warm') this run regenerates — required with --org-id")
    args = parser.parse_args()

    yelp_text = args.yelp_text
    if not yelp_text and args.yelp_text_file:
        yelp_text = Path(args.yelp_text_file).read_text(encoding="utf-8")

    rendered = generate_full_site(
        name=args.name,
        category=args.category,
        city=args.city,
        state=args.state,
        address=args.address,
        phone=args.phone,
        website=args.website,
        info_page_urls=args.info_pages,
        yelp_review_text=yelp_text,
        use_google_places=args.use_google,
        versions=args.versions,
        revision_notes=args.revision_notes,
        subject_id=args.subject_id,
        base_url=args.base_url,
        output_dir=Path(args.output_dir),
    )
    if not rendered:
        logger.error("No site generated — check --category is a recognized value.")
        return

    subject_id = rendered[0]["subject_id"]

    # Durable record — written directly by this process rather than left for
    # the webapp to do later, so it survives even if the webapp process
    # restarts before anyone looks at the result (see docs/setup_site_generator.md).
    if args.record_to_sheet:
        if args.org_id and args.theme:
            site_generator_state.record_regeneration(
                org_id=args.org_id, theme=args.theme, item=rendered[0],
                revision_notes=args.revision_notes,
            )
        else:
            site_generator_state.record_initial_generation(
                org_id=subject_id, name=args.name, category=args.category,
                city=args.city, state=args.state, address=args.address,
                rendered=rendered,
            )

    # Best-effort local handoff so the webapp can show "here's what was just
    # made" immediately after the job finishes. Not the source of truth —
    # the Sheet write above is — so it's fine if this never gets read (e.g.
    # the webapp process restarted in between).
    result_path = Path(args.output_dir) / "mocks" / subject_id / "result.json"
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps({
        "subject_id": subject_id,
        "name": args.name,
        "category": args.category,
        "city": args.city,
        "state": args.state,
        "revision_notes": args.revision_notes,
        "rendered": rendered,
    }, indent=2), encoding="utf-8")

    logger.info("Generated %d concept(s):", len(rendered))
    for item in rendered:
        logger.info("  %s: %s", item["label"], item["preview_url"])


if __name__ == "__main__":
    main()
