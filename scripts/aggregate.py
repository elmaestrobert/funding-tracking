#!/usr/bin/env python3
"""
Aggregator: reads normalized JSON drops from each tracker in sources/<tracker>/latest.json,
upserts into central/funding.sqlite, exports funding.csv, and regenerates site/index.html.

Expected upstream contract: each tracker writes a file at
    <shared>/sources/<tracker>/latest.json
containing a JSON array of opportunity objects. Fields are mapped via NORMALIZERS
below — add a new entry when onboarding a new tracker or when its schema changes.
"""
from __future__ import annotations

import csv
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES_DIR = ROOT / "sources"
CENTRAL_DIR = ROOT / "central"
SITE_DIR = ROOT / "docs"  # GitHub Pages requires '/' or '/docs'
DB_PATH = CENTRAL_DIR / "funding.sqlite"
CSV_PATH = CENTRAL_DIR / "funding.csv"
SCHEMA_PATH = CENTRAL_DIR / "schema.sql"

TRACKERS = ["global-dev", "ai-work", "ai-agents", "ai-eval"]

AMOUNT_RE = re.compile(r"\$?\s*([\d,.]+)\s*([kmb]?)", re.IGNORECASE)


def parse_amount(s):
    """Best-effort parse of an amount string into (min, max) USD floats."""
    if s is None:
        return None, None
    if isinstance(s, (int, float)):
        return float(s), float(s)
    text = str(s).strip()
    if not text:
        return None, None
    nums = []
    for m in AMOUNT_RE.finditer(text):
        raw, suffix = m.group(1), m.group(2).lower()
        try:
            val = float(raw.replace(",", ""))
        except ValueError:
            continue
        mult = {"k": 1e3, "m": 1e6, "b": 1e9, "": 1.0}[suffix]
        nums.append(val * mult)
    if not nums:
        return None, None
    return min(nums), max(nums)


def stable_id(row, tracker):
    """Pick a stable key per opportunity: prefer explicit id, else url, else slug(title)."""
    for key in ("id", "source_id", "uid", "slug"):
        if row.get(key):
            return str(row[key])
    if row.get("url"):
        return row["url"]
    title = (row.get("title") or "").strip().lower()
    return re.sub(r"[^a-z0-9]+", "-", title)[:80] or "unknown"


def normalize(row, tracker):
    """Map an upstream row to the unified schema."""
    title = row.get("title") or row.get("name") or row.get("opportunity") or "(untitled)"
    funder = row.get("funder") or row.get("organization") or row.get("sponsor")
    url = row.get("url") or row.get("link")
    deadline = row.get("deadline") or row.get("due_date") or row.get("close_date")
    rolling = 1 if (row.get("rolling") or (deadline and "rolling" in str(deadline).lower())) else 0
    if rolling:
        deadline = None
    amount_raw = row.get("amount") or row.get("award") or row.get("funding_amount")
    amt_min, amt_max = parse_amount(amount_raw)
    if row.get("amount_min") is not None:
        amt_min = row.get("amount_min")
    if row.get("amount_max") is not None:
        amt_max = row.get("amount_max")

    return {
        "source_tracker": tracker,
        "source_id": stable_id(row, tracker),
        "title": title,
        "funder": funder,
        "category": row.get("category") or row.get("topic"),
        "amount_min": amt_min,
        "amount_max": amt_max,
        "amount_raw": str(amount_raw) if amount_raw is not None else None,
        "currency": row.get("currency") or "USD",
        "deadline": deadline,
        "rolling": rolling,
        "status": row.get("status") or "open",
        "url": url,
        "description": row.get("description") or row.get("summary"),
        "thematic_fit": row.get("thematic_fit"),
        "broken_url": 1 if row.get("broken_url") else 0,
        "raw_json": json.dumps(row, ensure_ascii=False),
    }


def init_db(conn):
    # Some FUSE-mounted filesystems (used for Cowork shared folders) can't
    # handle SQLite's default WAL/journal files. MEMORY journaling sidesteps
    # that and is fine for this workload (single writer, small volume).
    conn.execute("PRAGMA journal_mode=MEMORY;")
    conn.execute("PRAGMA synchronous=OFF;")
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())


def upsert(conn, rec, now):
    cur = conn.execute(
        "SELECT id, date_added FROM opportunities WHERE source_tracker=? AND source_id=?",
        (rec["source_tracker"], rec["source_id"]),
    )
    existing = cur.fetchone()
    if existing:
        conn.execute(
            """UPDATE opportunities SET
                 title=?, funder=?, category=?, amount_min=?, amount_max=?, amount_raw=?,
                 currency=?, deadline=?, rolling=?, status=?, url=?, description=?,
                 thematic_fit=?, broken_url=?, last_seen=?, raw_json=?
               WHERE id=?""",
            (
                rec["title"], rec["funder"], rec["category"], rec["amount_min"], rec["amount_max"],
                rec["amount_raw"], rec["currency"], rec["deadline"], rec["rolling"], rec["status"],
                rec["url"], rec["description"], rec["thematic_fit"], rec["broken_url"], now,
                rec["raw_json"], existing[0],
            ),
        )
        return "updated"
    conn.execute(
        """INSERT INTO opportunities (
               source_tracker, source_id, title, funder, category, amount_min, amount_max,
               amount_raw, currency, deadline, rolling, status, url, description,
               thematic_fit, broken_url, date_added, last_seen, raw_json
           ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            rec["source_tracker"], rec["source_id"], rec["title"], rec["funder"], rec["category"],
            rec["amount_min"], rec["amount_max"], rec["amount_raw"], rec["currency"],
            rec["deadline"], rec["rolling"], rec["status"], rec["url"], rec["description"],
            rec["thematic_fit"], rec["broken_url"], now, now, rec["raw_json"],
        ),
    )
    return "new"


def load_tracker(conn, tracker, now):
    path = SOURCES_DIR / tracker / "latest.json"
    if not path.exists():
        conn.execute(
            "INSERT INTO runs(run_at,source_tracker,rows_read,rows_new,rows_updated,error) VALUES (?,?,?,?,?,?)",
            (now, tracker, 0, 0, 0, f"missing {path.name}"),
        )
        return 0, 0, 0
    try:
        rows = json.loads(path.read_text())
    except Exception as e:
        conn.execute(
            "INSERT INTO runs(run_at,source_tracker,rows_read,rows_new,rows_updated,error) VALUES (?,?,?,?,?,?)",
            (now, tracker, 0, 0, 0, f"parse error: {e}"),
        )
        return 0, 0, 0
    if not isinstance(rows, list):
        rows = rows.get("opportunities") or rows.get("data") or []
    new = upd = 0
    for row in rows:
        rec = normalize(row, tracker)
        result = upsert(conn, rec, now)
        if result == "new":
            new += 1
        else:
            upd += 1
    conn.execute(
        "INSERT INTO runs(run_at,source_tracker,rows_read,rows_new,rows_updated,error) VALUES (?,?,?,?,?,?)",
        (now, tracker, len(rows), new, upd, None),
    )
    return len(rows), new, upd


def export_csv(conn):
    cur = conn.execute(
        """SELECT source_tracker, title, funder, category, amount_min, amount_max, amount_raw,
                  currency, deadline, rolling, status, url, description, thematic_fit,
                  broken_url, date_added, last_seen
             FROM opportunities ORDER BY last_seen DESC, deadline ASC"""
    )
    cols = [d[0] for d in cur.description]
    with open(CSV_PATH, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for row in cur:
            w.writerow(row)


def export_json_for_site(conn):
    cur = conn.execute(
        """SELECT source_tracker, title, funder, category, amount_raw, deadline, rolling,
                  status, url, description, thematic_fit, broken_url, date_added, last_seen
             FROM opportunities ORDER BY
               CASE WHEN deadline IS NULL THEN 1 ELSE 0 END, deadline ASC"""
    )
    cols = [d[0] for d in cur.description]
    data = [dict(zip(cols, row)) for row in cur]
    (SITE_DIR / "data.json").write_text(json.dumps(data, ensure_ascii=False, indent=2))


def _open_db():
    """Open the central DB, tolerating FUSE filesystems (Cowork mounts).

    Some FUSE backends reject SQLite's fcntl/locking calls. In that case we
    build the DB in a local temp dir and copy it into place at the end.
    Returns (conn, final_path, working_path).
    """
    try:
        conn = sqlite3.connect(str(DB_PATH))
        # Force an actual write so we hit any FUSE locking issue early.
        conn.execute("CREATE TABLE IF NOT EXISTS _probe (x INTEGER)")
        conn.execute("DROP TABLE _probe")
        conn.commit()
        return conn, DB_PATH, DB_PATH
    except sqlite3.OperationalError:
        try:
            conn.close()
        except Exception:
            pass
        # Copy any existing DB into /tmp so we preserve history, then work there.
        tmp = Path(tempfile.mkdtemp(prefix="funding_agg_")) / "funding.sqlite"
        if DB_PATH.exists():
            try:
                shutil.copy2(DB_PATH, tmp)
            except OSError:
                pass
        conn = sqlite3.connect(str(tmp))
        return conn, DB_PATH, tmp


def main():
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn, final_db, working_db = _open_db()
    try:
        init_db(conn)
        totals = {"read": 0, "new": 0, "upd": 0}
        for tracker in TRACKERS:
            read, new, upd = load_tracker(conn, tracker, now)
            totals["read"] += read
            totals["new"] += new
            totals["upd"] += upd
            print(f"  {tracker:12s}  read={read:4d}  new={new:4d}  updated={upd:4d}")
        conn.commit()
        export_csv(conn)
        export_json_for_site(conn)
    finally:
        conn.close()
    if working_db != final_db:
        shutil.copy2(working_db, final_db)
        try:
            shutil.rmtree(working_db.parent)
        except OSError:
            pass
    print(f"TOTAL read={totals['read']} new={totals['new']} updated={totals['upd']}")
    print(f"DB:  {final_db}")
    print(f"CSV: {CSV_PATH}")


if __name__ == "__main__":
    sys.exit(main())
