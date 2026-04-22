# scripts/debug_status_counts.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collections import Counter
from src import sheets, config

rows = sheets.read_all_rows(config.TAB_LEADS)
print("Status counts:", Counter(r.get("status", "") for r in rows))
