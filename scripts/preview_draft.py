import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import sheets, config, drafter

rows = sheets.read_all_rows(config.TAB_LEADS)
ready = [r for r in rows if r.get("status") == "ready_to_send"]

for lead in ready[:2]:
    rendered = drafter.render_email(lead)
    if not rendered:
        continue
    print("=" * 70)
    print(f"SCHOOL: {lead.get('name')}")
    print(f"TO: {lead.get('best_email')}")
    print(f"SUBJECT: {rendered.subject}")
    print(f"TEMPLATE: {rendered.template_id}")
    print("-" * 70)
    print(rendered.html_body)
    print()
    