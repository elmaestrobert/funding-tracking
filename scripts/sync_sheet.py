#!/usr/bin/env python3
"""
Mirror central/funding.sqlite into a Google Sheet.

Setup (one-time):
  1. Create a Google Cloud project and enable the Google Sheets API + Drive API.
  2. Create a service account, download its JSON key, save as
     central/google-credentials.json.
  3. Create a Google Sheet, share it with the service account email as Editor,
     and put the sheet ID in central/google-sheet-id.txt.
  4. pip install gspread

The script writes one worksheet ("opportunities") with the same columns as
central/funding.csv, replacing all rows on each run.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "central" / "funding.sqlite"
CREDS_PATH = ROOT / "central" / "google-credentials.json"
SHEET_ID_PATH = ROOT / "central" / "google-sheet-id.txt"
WORKSHEET_NAME = "opportunities"

COLUMNS = [
    "source_tracker", "title", "funder", "category",
    "amount_min", "amount_max", "amount_raw", "currency",
    "deadline", "rolling", "status", "url", "description",
    "thematic_fit", "broken_url", "date_added", "last_seen",
]


def main():
    if not CREDS_PATH.exists():
        sys.exit(f"Missing credentials file: {CREDS_PATH}\nSee README Phase 4.")
    if not SHEET_ID_PATH.exists():
        sys.exit(f"Missing sheet id file: {SHEET_ID_PATH}\nSee README Phase 4.")
    sheet_id = SHEET_ID_PATH.read_text().strip()
    if not sheet_id:
        sys.exit(f"Sheet id file is empty: {SHEET_ID_PATH}")

    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        sys.exit("gspread not installed. Run: pip3 install gspread google-auth")

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(str(CREDS_PATH), scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(sheet_id)

    # Pull rows from SQLite.
    conn = sqlite3.connect(str(DB_PATH))
    try:
        cur = conn.execute(
            f"SELECT {','.join(COLUMNS)} FROM opportunities "
            f"ORDER BY CASE WHEN deadline IS NULL THEN 1 ELSE 0 END, deadline ASC"
        )
        rows = [list(r) for r in cur.fetchall()]
    finally:
        conn.close()

    # Get-or-create the worksheet.
    try:
        ws = sh.worksheet(WORKSHEET_NAME)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=WORKSHEET_NAME, rows=max(len(rows) + 10, 100), cols=len(COLUMNS))

    ws.clear()
    ws.update([COLUMNS] + rows, value_input_option="RAW")
    ws.freeze(rows=1)
    print(f"Synced {len(rows)} rows to sheet {sheet_id} / worksheet '{WORKSHEET_NAME}'")


if __name__ == "__main__":
    main()
