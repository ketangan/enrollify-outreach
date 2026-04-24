"""
Reset manual-review leads back to ready_for_owner_lookup so the improved
owner finder can take another pass.
Only resets leads that were rejected by Phase 4 (not Phase 3 fetch-failures).
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gspread.utils import rowcol_to_a1
from src import sheets, config

ws = sheets.get_tab(config.TAB_LEADS)
all_rows = ws.get_all_values()
headers = all_rows[0]
status_col = headers.index("status") + 1
last_action_col = headers.index("last_action") + 1

updates = []
for i, row in enumerate(all_rows[1:], start=2):
    if len(row) < max(status_col, last_action_col):
        continue
    status = row[status_col - 1]
    last_action = row[last_action_col - 1]
    if status == "needs_manual_review" and last_action == "phase4_owner_found":
        updates.append({
            "range": rowcol_to_a1(i, status_col),
            "values": [["ready_for_owner_lookup"]],
        })

print(f"Found {len(updates)} leads to reset.")
if updates:
    response = input("Reset them? [y/N]: ")
    if response.lower() == "y":
        ws.batch_update(updates, value_input_option="USER_ENTERED")
        print("Done. Run python scripts/run_phase_4_owners.py to re-process.")
    else:
        print("Aborted.")
else:
    print("Nothing to reset.")
    