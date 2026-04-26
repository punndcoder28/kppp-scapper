"""
One-time script to generate Gmail OAuth refresh token.

Run this locally once:
    python scripts/get_token.py

It will open a browser for you to approve access, then save token.json
in the project root. Copy the refresh_token value into GitHub Secrets
as GMAIL_REFRESH_TOKEN.

Also add to GitHub Secrets:
    GMAIL_CLIENT_ID      — from credentials.json
    GMAIL_CLIENT_SECRET  — from credentials.json
"""

import json
import os
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
CREDENTIALS_FILE = Path(__file__).parent.parent / "credentials.json"
TOKEN_FILE = Path(__file__).parent.parent / "token.json"


def main():
    if not CREDENTIALS_FILE.exists():
        print(f"ERROR: credentials.json not found at {CREDENTIALS_FILE}")
        sys.exit(1)

    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
    creds = flow.run_local_server(port=0)

    TOKEN_FILE.write_text(creds.to_json())
    print(f"\ntoken.json written to {TOKEN_FILE}")

    token_data = json.loads(creds.to_json())
    print("\n── Values to add as GitHub Actions secrets ──")
    print(f"GMAIL_REFRESH_TOKEN  = {token_data.get('refresh_token')}")

    creds_data = json.loads(CREDENTIALS_FILE.read_text())
    installed = creds_data.get("installed", {})
    print(f"GMAIL_CLIENT_ID      = {installed.get('client_id')}")
    print(f"GMAIL_CLIENT_SECRET  = {installed.get('client_secret')}")
    print("\nDo NOT commit credentials.json or token.json to git.")


if __name__ == "__main__":
    main()
