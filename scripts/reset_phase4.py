# scripts/reset_phase4.py
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
    if len(row) < status_col:
        continue
    current = row[status_col - 1]
    last_action = row[last_action_col - 1] if len(row) >= last_action_col else ""
    if last_action == "phase4_owner_found":
        updates.append({
            "range": rowcol_to_a1(i, status_col),
            "values": [["ready_for_owner_lookup"]],
        })

if updates:
    ws.batch_update(updates, value_input_option="USER_ENTERED")
    print(f"Reset {len(updates)} rows.")
else:
    print("Nothing to reset.")
    