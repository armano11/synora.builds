"""Gmail OAuth setup — run once to generate token.json.

Usage:
    python setup_gmail.py

Opens a browser, you authorize the app, token.json is saved next to credentials.json.
After that, the Gmail poller and drafter work automatically.
"""
from __future__ import annotations

import json
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.compose",
]

CREDENTIALS_PATH = Path(__file__).resolve().parent / "credentials.json"
TOKEN_PATH = Path(__file__).resolve().parent / "token.json"


def main() -> int:
    if not CREDENTIALS_PATH.exists():
        print(f"Error: {CREDENTIALS_PATH} not found")
        return 1

    print("Starting Gmail OAuth flow...")
    print("A browser window will open. Authorize the app to continue.\n")

    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), SCOPES)
    creds = flow.run_local_server(port=0)

    TOKEN_PATH.write_text(creds.to_json())
    print(f"\ntoken.json saved to {TOKEN_PATH}")
    print("Gmail integration is now ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
