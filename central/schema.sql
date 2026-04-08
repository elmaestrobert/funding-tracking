-- Unified funding opportunity schema
-- One row per (source_tracker, source_id) pair. Aggregator upserts on this key.

CREATE TABLE IF NOT EXISTS opportunities (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    source_tracker   TEXT NOT NULL,     -- 'global-dev' | 'ai-work' | 'ai-agents' | 'ai-eval'
    source_id        TEXT NOT NULL,     -- stable ID from the upstream tracker (slug/hash/url)
    title            TEXT NOT NULL,
    funder           TEXT,
    category         TEXT,              -- free-text topic tag from upstream
    amount_min       REAL,              -- USD
    amount_max       REAL,              -- USD
    amount_raw       TEXT,              -- original amount string, for display
    currency         TEXT DEFAULT 'USD',
    deadline         TEXT,              -- ISO date or NULL if rolling
    rolling          INTEGER DEFAULT 0, -- 1 if rolling, else 0
    status           TEXT DEFAULT 'open', -- 'open' | 'closed' | 'upcoming'
    url              TEXT,
    description      TEXT,
    thematic_fit     INTEGER,           -- 1-5 from upstream if provided
    broken_url       INTEGER DEFAULT 0,
    date_added       TEXT NOT NULL,     -- ISO timestamp when first seen
    last_seen        TEXT NOT NULL,     -- ISO timestamp of most recent aggregator run that saw it
    raw_json         TEXT,              -- original upstream row as JSON, for debugging
    UNIQUE(source_tracker, source_id)
);

CREATE INDEX IF NOT EXISTS idx_source ON opportunities(source_tracker);
CREATE INDEX IF NOT EXISTS idx_deadline ON opportunities(deadline);
CREATE INDEX IF NOT EXISTS idx_status ON opportunities(status);
CREATE INDEX IF NOT EXISTS idx_last_seen ON opportunities(last_seen);

-- Run log so we can see when the aggregator ran and what it found
CREATE TABLE IF NOT EXISTS runs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at         TEXT NOT NULL,
    source_tracker TEXT NOT NULL,
    rows_read      INTEGER,
    rows_new       INTEGER,
    rows_updated   INTEGER,
    error          TEXT
);
