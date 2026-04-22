import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gspread.utils import rowcol_to_a1
from src import sheets, config

RESET_STATUSES = {"needs_manual_review", "ready_for_owner_lookup", "online_system_exclude"}

ws = sheets.get_tab(config.TAB_LEADS)
all_rows = ws.get_all_values()
headers = all_rows[0]
status_col = headers.index("status") + 1
last_action_col = headers.index("last_action") + 1

updates = []
for i, row in enumerate(all_rows[1:], start=2):
    if len(row) < status_col:
        continue
    current = row[status_col - 1]
    last_action = row[last_action_col - 1] if len(row) >= last_action_col else ""
    # Only reset rows that were classified by phase3 (don't touch legitimately set statuses)
    if current in RESET_STATUSES and last_action == "phase3_classified":
        updates.append({
            "range": rowcol_to_a1(i, status_col),
            "values": [["pending_classify"]],
        })

if updates:
    ws.batch_update(updates, value_input_option="USER_ENTERED")
    print(f"Reset {len(updates)} rows to pending_classify.")
else:
    print("Nothing to reset.")
    