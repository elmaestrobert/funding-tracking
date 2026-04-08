# Funding Tracking — Unified Pipeline

A central database + dashboard that unifies four daily funding trackers:
**global-dev**, **ai-work**, **ai-agents**, and **ai-eval**.

## Layout

```
funding_tracking/
├── sources/                # one folder per upstream tracker
│   ├── global-dev/latest.json
│   ├── ai-work/latest.json
│   ├── ai-agents/latest.json
│   └── ai-eval/latest.json
├── central/
│   ├── schema.sql          # unified SQLite schema
│   ├── funding.sqlite      # source-of-truth DB (built by aggregator)
│   └── funding.csv         # exported for GitHub / general consumption
├── site/
│   ├── index.html          # GitHub Pages dashboard (filter, sort, search)
│   └── data.json           # rendered data the dashboard reads
└── scripts/
    └── aggregate.py        # reads all sources, upserts SQLite, exports CSV+JSON
```

## Run it

```bash
cd /Users/robertpraas/funding_tracking
python3 scripts/aggregate.py
open site/index.html
```

The aggregator is idempotent — running it multiple times only updates `last_seen`
on rows it has already seen and inserts truly new ones.

## Unified schema

Every opportunity has a stable key `(source_tracker, source_id)`. The aggregator
upserts on that key, so a tracker can drop a fresh `latest.json` daily and the
DB never duplicates rows. Key columns:

| column          | notes                                                  |
|-----------------|--------------------------------------------------------|
| source_tracker  | one of `global-dev`, `ai-work`, `ai-agents`, `ai-eval` |
| source_id       | stable per opportunity (id / url / slug)               |
| title, funder   | display fields                                         |
| amount_min/max  | parsed USD floats; original kept in `amount_raw`       |
| deadline        | ISO date or NULL if `rolling=1`                        |
| status          | `open` / `closed` / `upcoming`                         |
| thematic_fit    | 1–5 if upstream provides it                            |
| date_added      | first time the aggregator saw this row                 |
| last_seen       | most recent run that re-saw it                         |
| raw_json        | original upstream row, for debugging                   |

See `central/schema.sql` for the full DDL.

## Upstream contract

Each tracker is responsible for writing one file:

```
sources/<tracker>/latest.json
```

…containing a JSON array of opportunity objects. The aggregator's `normalize()`
function in `scripts/aggregate.py` is permissive — it accepts common synonyms
(`title`/`name`, `funder`/`organization`, `url`/`link`, `deadline`/`due_date`,
`amount`/`award`, etc.) and parses amount strings like `"$1M–$5M"` into numeric
ranges. To onboard a new tracker, drop a `latest.json` in a new subfolder and
add the folder name to `TRACKERS` in `aggregate.py`.

---

## Phase 2 — wiring up the four existing scheduled tasks

The four trackers currently run in isolated Cowork sessions and write to
session-local outputs. To feed this pipeline they need to also drop a
normalized `latest.json` into `sources/<tracker>/`. Plan:

1. **Mount this folder into each tracker's session.** In each tracker's session,
   the user (Robert) calls "use the cowork directory tool" pointing at
   `~/funding_tracking`. Once mounted, the tracker can write to it.
2. **Update each tracker's prompt** (via `update_scheduled_task`) to add a
   final step:
   > After producing your normal outputs, also write a normalized JSON array of
   > all currently-active opportunities to
   > `/Users/robertpraas/funding_tracking/sources/<TRACKER>/latest.json`. Use
   > the field names `id`, `title`, `funder`, `category`, `amount`, `deadline`,
   > `rolling`, `status`, `url`, `description`, `thematic_fit`. One object per
   > opportunity, JSON array at the top level.
3. **Create the aggregator scheduled task** (5th task) that runs ~30 min after
   the latest upstream tracker finishes (suggested cron `30 9 * * *` daily).
   It runs `python3 scripts/aggregate.py` and then commits + pushes the
   `central/` and `site/` folders to the GitHub repo.

## Phase 3 — GitHub Pages publishing

Once you're ready to publish:

```bash
cd /Users/robertpraas/funding_tracking
git init -b main
git add .
git commit -m "Initial unified funding tracker"
gh repo create funding-tracking --public --source . --remote origin --push
gh repo edit --enable-pages --pages-branch main --pages-path /site
```

The dashboard will then live at
`https://<your-github-username>.github.io/funding-tracking/`.

The aggregator scheduled task should append a `git add . && git commit -m
"daily update $(date +%F)" && git push` step after running.

## Phase 4 — Google Sheet mirror

Walkthrough for setting up a service account with read/write access to a single
sheet:

1. Go to <https://console.cloud.google.com/projectcreate>, create a project
   called `funding-tracking`.
2. Enable the Google Sheets API: APIs & Services → Library → "Google Sheets
   API" → Enable. Same for "Google Drive API".
3. APIs & Services → Credentials → Create credentials → Service account.
   Name it `funding-aggregator`. Skip role assignment. Done.
4. Open the new service account → Keys → Add key → Create new key → JSON.
   Save the downloaded file as
   `/Users/robertpraas/funding_tracking/central/google-credentials.json`.
   **Do not commit this file** — it's in `.gitignore`.
5. Note the service account email (looks like
   `funding-aggregator@<project>.iam.gserviceaccount.com`).
6. Create a new Google Sheet, name it "Funding Tracker", and **share** it with
   that service account email as Editor.
7. Copy the sheet ID from its URL and add it to
   `central/google-sheet-id.txt`.
8. Run `python3 scripts/sync_sheet.py` (will be added in Phase 4 — uses
   `gspread`).

## Mock data note

The current `sources/*/latest.json` files are **mock data** used to verify the
aggregator end-to-end. They will be overwritten the first time the real
trackers write to them.
