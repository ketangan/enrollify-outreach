"""
Find leads previously marked online_system_exclude whose classifier reason
mentions a hosted form service (Google Forms, Jotform, Typeform, etc.).
Reset them to pending_classify so they get re-run under the new classifier.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gspread.utils import rowcol_to_a1
from src import sheets, config

# Keywords in the `notes` column that indicate a hosted-form signal
HOSTED_FORM_KEYWORDS = [
    "google form", "google forms", "docs.google.com", "forms.gle",
    "jotform", "typeform", "formstack", "wufoo", "cognito",
]

ws = sheets.get_tab(config.TAB_LEADS)
all_rows = ws.get_all_values()
headers = all_rows[0]
status_col = headers.index("status") + 1
notes_col = headers.index("notes") + 1
last_action_col = headers.index("last_action") + 1

candidates = []
for i, row in enumerate(all_rows[1:], start=2):
    if len(row) < max(status_col, notes_col):
        continue
    status = row[status_col - 1]
    notes = row[notes_col - 1].lower()
    if status != "online_system_exclude":
        continue
    if any(kw in notes for kw in HOSTED_FORM_KEYWORDS):
        candidates.append(i)

print(f"Found {len(candidates)} leads to reset.")
for row_idx in candidates[:10]:
    name = all_rows[row_idx - 1][headers.index("name")]
    notes = all_rows[row_idx - 1][notes_col - 1][:100]
    print(f"  row {row_idx}: {name} — {notes}")

if len(candidates) > 10:
    print(f"  ... and {len(candidates) - 10} more")

if not candidates:
    print("Nothing to reset.")
    sys.exit()

response = input(f"\nReset these {len(candidates)} leads to pending_classify? [y/N]: ")
if response.lower() != "y":
    print("Aborted.")
    sys.exit()

updates = []
for row_idx in candidates:
    updates.append({
        "range": rowcol_to_a1(row_idx, status_col),
        "values": [["pending_classify"]],
    })
    updates.append({
        "range": rowcol_to_a1(row_idx, last_action_col),
        "values": [["reset_for_third_party_reclass"]],
    })

ws.batch_update(updates, value_input_option="USER_ENTERED")
print(f"Reset {len(candidates)} leads. Re-run Phase 3 now.")