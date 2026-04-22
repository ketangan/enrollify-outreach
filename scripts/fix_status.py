import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gspread.utils import rowcol_to_a1
from src import sheets, config

ws = sheets.get_tab(config.TAB_LEADS)
all_rows = ws.get_all_values()
headers = all_rows[0]
status_col_idx = headers.index("status") + 1  # 1-indexed

updates = []
for i, row in enumerate(all_rows[1:], start=2):
    current = row[status_col_idx - 1] if len(row) >= status_col_idx else ""
    if not current.strip():
        a1 = rowcol_to_a1(i, status_col_idx)
        updates.append({"range": a1, "values": [["pending_classify"]]})

if updates:
    ws.batch_update(updates, value_input_option="USER_ENTERED")
    print(f"Fixed {len(updates)} rows in one batch.")
else:
    print("Nothing to fix.")