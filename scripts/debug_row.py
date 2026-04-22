import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import sheets, config

ws = sheets.get_tab(config.TAB_LEADS)
row = ws.row_values(2)
headers = sheets.get_headers(config.TAB_LEADS)
for h, v in zip(headers, row):
    print(f"{h!r}: {v!r}")