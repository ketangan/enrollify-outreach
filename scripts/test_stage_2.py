from anthropic import Anthropic
import sys
import random

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config, owner_finder, sheets

client = Anthropic(api_key=config.ANTHROPIC_API_KEY)

# Find a lead where Stage 1 left owner_name empty
rows = sheets.read_all_rows(config.TAB_LEADS)

candidates = [
    r for r in rows
    if r.get("status") == "needs_manual_review"
    and not (r.get("owner_name") or "").strip()
    and (r.get("website") or "").strip()
]
print(f"Found {len(candidates)} candidate leads. Picking one at random.")
test_lead = random.choice(candidates) if candidates else None

if not test_lead:
    print("No suitable test lead found.")
else:
    print(f"Testing on: {test_lead.get('name')} ({test_lead.get('website')})")
    result = owner_finder.find_owner(
        website=test_lead.get("website", ""),
        client=client,
        name=test_lead.get("name", ""),
        category=test_lead.get("category", ""),
        city=test_lead.get("city", ""),
        state=test_lead.get("state", ""),
    )
    print()
    print("Result:")
    print(f"  owner_name:       {result.owner_name}")
    print(f"  owner_title:      {result.owner_title}")
    print(f"  owner_source_url: {result.owner_source_url}")
    print(f"  best_email:       {result.best_email}")
    print(f"  email_confidence: {result.email_confidence}")
    print(f"  reason:           {result.reason}")
