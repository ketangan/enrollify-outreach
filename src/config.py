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

# --- Zoho ---
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
    