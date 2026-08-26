"""
Central configuration loader.
Reads .env, exposes constants used throughout the pipeline.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


# --- API keys / secrets ---
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
GOOGLE_PLACES_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY")
GOOGLE_SHEETS_CREDENTIALS_PATH = os.getenv(
    "GOOGLE_SHEETS_CREDENTIALS_PATH",
    str(PROJECT_ROOT / "config" / "google-service-account.json"),
)
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")

# --- Brand / outreach mailbox ---
BRAND_NAME = os.getenv("BRAND_NAME", "Pontora")
PRODUCT_DOMAIN = os.getenv("PRODUCT_DOMAIN", "mypontora.com")
PRODUCT_URL = os.getenv("PRODUCT_URL", f"https://{PRODUCT_DOMAIN}")
DEMO_URL = os.getenv("DEMO_URL", f"{PRODUCT_URL}/demo")
OUTREACH_EMAIL = os.getenv("OUTREACH_EMAIL", "ketan@mypontora.com")
OUTREACH_DOMAIN = OUTREACH_EMAIL.split("@")[-1] if "@" in OUTREACH_EMAIL else PRODUCT_DOMAIN
SUMMARY_EMAIL_TO = os.getenv("SUMMARY_EMAIL_TO", "kg.ketan@gmail.com")
OUTREACH_ADMIN_URL = os.getenv(
    "OUTREACH_ADMIN_URL",
    "https://enrollify-admin.onrender.com",
)

# Optional website-mock upsell flow. Keep these optional so daily outreach does
# not break before the Cloudflare mock site is configured.
WEBSITE_MOCK_BASE_URL = os.getenv("WEBSITE_MOCK_BASE_URL", "")
CLICK_LOGGER_URL = (
    os.getenv("CLICK_LOGGER_URL")
    or "https://script.google.com/macros/s/AKfycbxC_jG6QI9cuXYNRWfq1nn0fJlUTCkeAJmx_x4k24QlN6-if-pTjq5UrOsxaHCHy7td/exec"
)

# Full-site generator webapp page. This route triggers real Places API and
# Claude API spend on every submit and the webapp itself has no other auth —
# unset means the route refuses to serve rather than being open to anyone
# who finds the URL.
SITE_GENERATOR_ACCESS_KEY = os.getenv("SITE_GENERATOR_ACCESS_KEY", "")

# Cloudflare R2 storage for generated full-site output. Durable across
# Render restarts/redeploys, unlike local disk — see docs/setup_site_generator.md.
R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID", "")
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID", "")
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY", "")
R2_BUCKET_NAME = os.getenv("R2_BUCKET_NAME", "pontora-generated-sites")
GENERATED_SITES_BASE_URL = os.getenv("GENERATED_SITES_BASE_URL", "")

# Cloudflare KV-backed short links (sites.mypontora.com/p/<code>) for the
# text-message box on generated sites — a self-hosted shortener so the
# link a prospect gets in a text is on-brand, not a third-party domain.
# Separate from the R2 credentials above: KV writes go through Cloudflare's
# regular API (a scoped API token), not the S3-compatible R2 API.
CLOUDFLARE_API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN", "")
CLOUDFLARE_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID", "") or R2_ACCOUNT_ID
CLOUDFLARE_KV_NAMESPACE_ID = os.getenv("CLOUDFLARE_KV_NAMESPACE_ID", "")

# --- Gmail OAuth ---
GMAIL_OAUTH_CLIENT_PATH = os.getenv(
    "GMAIL_OAUTH_CLIENT_PATH",
    str(PROJECT_ROOT / "config" / "gmail-oauth-client.json"),
)
GMAIL_TOKEN_PATH = os.getenv(
    "GMAIL_TOKEN_PATH",
    str(PROJECT_ROOT / "config" / "gmail-token.json"),
)

# --- Legacy Zoho (kept temporarily until Gmail workflow is verified end-to-end) ---
ZOHO_EMAIL = os.getenv("ZOHO_EMAIL")
ZOHO_APP_PASSWORD = os.getenv("ZOHO_APP_PASSWORD")
ZOHO_IMAP_HOST = os.getenv("ZOHO_IMAP_HOST", "imap.zoho.com")
ZOHO_IMAP_PORT = int(os.getenv("ZOHO_IMAP_PORT", "993"))
ZOHO_SMTP_HOST = os.getenv("ZOHO_SMTP_HOST", "smtp.zoho.com")
ZOHO_SMTP_PORT = int(os.getenv("ZOHO_SMTP_PORT", "465"))

# --- Pushover (Phase 6) ---
PUSHOVER_USER_KEY = os.getenv("PUSHOVER_USER_KEY")
PUSHOVER_APP_TOKEN = os.getenv("PUSHOVER_APP_TOKEN")

# --- Project config ---
DEFAULT_DAILY_EMAIL_CAP = int(os.getenv("DEFAULT_DAILY_EMAIL_CAP", "20"))
WORKING_HOURS_START = int(os.getenv("WORKING_HOURS_START", "9"))
WORKING_HOURS_END = int(os.getenv("WORKING_HOURS_END", "17"))
TIMEZONE = os.getenv("TIMEZONE", "America/Los_Angeles")
HOME_ZIP = os.getenv("HOME_ZIP", "90045")
ACTIVE_OUTREACH_START_DATE = os.getenv("ACTIVE_OUTREACH_START_DATE", "2026-07-29")


# --- Categories we search ---
SCHOOL_CATEGORIES = [
    "dance",
    "music",
    "sports",
    "preschool",
    "daycare",
    "martial_arts",
    "art",
    "gymnastics",
    "swim",
    "tutoring",
    "language",
    "coding_stem",
    "montessori",
]

# Maps our internal category id to the search phrase sent to Google Places
CATEGORY_SEARCH_PHRASES = {
    "dance": "dance studio",
    "music": "music school",
    "sports": "sports academy for kids",
    "preschool": "preschool",
    "daycare": "daycare",
    "martial_arts": "martial arts school",
    "art": "art studio for kids",
    "gymnastics": "gymnastics academy",
    "swim": "swim school",
    "tutoring": "tutoring center",
    "language": "language school for kids",
    "coding_stem": "coding school for kids",
    "montessori": "montessori school",
}


# --- Sheet tab names ---
TAB_LEADS = "Leads"
TAB_ALREADY_CONTACTED = "Already_Contacted"
TAB_COVERAGE = "Coverage"
TAB_TEMPLATES = "Templates"
TAB_NO_WEBSITE = "No_Website_Schools"
TAB_ARCHIVE = "Archive"


def validate():
    """Fail fast if required secrets are missing."""
    required = {
        "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY,
        "GOOGLE_PLACES_API_KEY": GOOGLE_PLACES_API_KEY,
        "GOOGLE_SHEET_ID": GOOGLE_SHEET_ID,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        raise RuntimeError(
            f"Missing required .env values: {', '.join(missing)}"
        )
    if not Path(GOOGLE_SHEETS_CREDENTIALS_PATH).exists():
        raise RuntimeError(
            f"Google service account JSON not found at {GOOGLE_SHEETS_CREDENTIALS_PATH}"
        )
