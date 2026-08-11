# Click Log View

The live click logger writes raw rows to `Click_Log`. It is a Google Apps Script
web app, so changing the row before it is written would require redeploying that
logger.

The no-deploy fix is `Click_Log_View`: a formula-backed Google Sheets tab that
reads raw `Click_Log` rows and resolves `lead_id` to `school_name` and `website`
from `Leads` and `Archive`.

Set it up once:

```bash
python scripts/setup_click_log_view.py
```

After that, review `Click_Log_View` instead of raw `Click_Log`. New click rows
should appear there automatically without rerunning `backfill_click_log.py`.

`backfill_click_log.py` still exists for one-off cleanup when you specifically
want to write the resolved values back into the raw `Click_Log` tab:

```bash
python scripts/backfill_click_log.py --dry-run
python scripts/backfill_click_log.py
```
