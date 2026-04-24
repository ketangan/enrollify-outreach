import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collections import Counter
from src import sheets, config

rows = sheets.read_all_rows(config.TAB_LEADS)
review = [r for r in rows
          if r.get("status") == "needs_manual_review"
          and r.get("last_action") == "phase4_owner_found"]

print(f"Total manual-review leads: {len(review)}\n")

# Bucket the notes to see what Haiku is reporting
reason_buckets = Counter()
for r in review:
    notes = str(r.get("notes", "")).lower()
    if "no_content_or_emails" in notes or "no content" in notes:
        reason_buckets["no content or no emails"] += 1
    elif "fetch_failed" in notes:
        reason_buckets["fetch failed"] += 1
    elif "parse_error" in notes or "llm_error" in notes:
        reason_buckets["LLM error"] += 1
    elif "only" in notes and "emails" in notes:
        reason_buckets["emails found but weak"] += 1
    else:
        reason_buckets["other (see samples)"] += 1

print("Reason buckets:")
for bucket, count in reason_buckets.most_common():
    print(f"  {bucket}: {count}")

print("\nSample rows (first 10):")
for r in review[:10]:
    print(f"  {r.get('name', '')[:50]}")
    print(f"    website: {r.get('website', '')}")
    print(f"    owner: {r.get('owner_name', '')!r}")
    print(f"    email: {r.get('best_email', '')!r}")
    print(f"    notes: {r.get('notes', '')[:150]}")
    print()