# scripts/retry_unverified_phase4.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import sheets, config

leads_ws = sheets.get_tab(config.TAB_LEADS)
all_rows = leads_ws.get_all_values()
headers = all_rows[0]
status_col = headers.index("status")
email_conf_col = headers.index("email_confidence")

count = 0
for i, row in enumerate(all_rows[1:], start=2):
    if len(row) <= max(status_col, email_conf_col):
        continue
    if row[status_col] == "needs_manual_review" and row[email_conf_col] == "unverified":
        leads_ws.update_cell(i, status_col + 1, "ready_for_owner_lookup")
        count += 1

print(f"Reset {count} unverified rows back to ready_for_owner_lookup.")