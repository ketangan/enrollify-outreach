#!/usr/bin/env python3
"""
Generate the local Gmail OAuth token for the Pontora outreach mailbox.

Prerequisite:
  config/gmail-oauth-client.json exists and was downloaded from the Google
  Cloud OAuth Desktop client.

Usage:
  python scripts/setup_gmail_oauth.py

When the authorization URL prints, open it and sign in as the configured
OUTREACH_EMAIL (`ketan@mypontora.com` by default), not a personal Gmail account.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src import config
from src.gmail_client import GMAIL_SCOPES


def main() -> None:
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as e:
        raise SystemExit(
            "Missing Gmail OAuth dependencies. Run: pip install -r requirements.txt"
        ) from e

    client_path = Path(config.GMAIL_OAUTH_CLIENT_PATH)
    token_path = Path(config.GMAIL_TOKEN_PATH)

    if not client_path.exists():
        raise SystemExit(
            f"OAuth client JSON not found at {client_path}\n"
            "Download the Desktop OAuth client JSON from Google Cloud and save it there."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(client_path), GMAIL_SCOPES)
    creds = flow.run_local_server(
        port=0,
        open_browser=False,
        access_type="offline",
        prompt="select_account consent",
        authorization_prompt_message=(
            "Open this URL, authorize the Pontora mailbox, and leave this "
            "terminal running until Google redirects back:\n{url}\n"
        ),
    )

    service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    profile = service.users().getProfile(userId="me").execute()
    authorized_email = str(profile.get("emailAddress", "")).strip().lower()
    expected_email = config.OUTREACH_EMAIL.lower()

    if authorized_email != expected_email:
        raise SystemExit(
            "Wrong Gmail account authorized.\n"
            f"Expected: {expected_email}\n"
            f"Got:      {authorized_email}\n"
            "No token was saved. Re-run this script and choose the Pontora mailbox."
        )

    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json(), encoding="utf-8")

    print(f"Gmail OAuth token saved to {token_path}")
    print(f"Authorized mailbox: {authorized_email}")
    print("Do not commit this file.")


if __name__ == "__main__":
    main()
