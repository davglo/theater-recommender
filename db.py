"""SQLite schema and access layer for theater-recommender."""
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import config

logger = logging.getLogger(__name__)

VALID_STATUSES = ("seen", "not_interested", "pending", "watchlist")
DECISION_STATUSES = ("seen", "not_interested", "watchlist")  # count toward re-derivation

SCHEMA = """
CREATE TABLE IF NOT EXISTS titles (
    tmdb_id      INTEGER NOT NULL,
    media_type   TEXT NOT NULL CHECK (media_type IN ('movie','tv')),
    title        TEXT NOT NULL,
    year         INTEGER,
    genres       TEXT,            -- JSON array
    keywords     TEXT,            -- JSON array
    poster_path  TEXT,
    overview     TEXT,
    tmdb_rating  REAL,
    release_date TEXT,            -- YYYY-MM-DD, TMDB release_date/first_air_date
    trailer_url  TEXT,            -- YouTube URL, nullable
    original_language TEXT,       -- ISO 639-1, e.g. 'en'
    popularity   REAL,            -- TMDB popularity at last fetch (buzz signal)
    recent_date  TEXT,            -- last 'release': movie release / TV latest season premiere
    latest_season INTEGER,        -- TV: season that recent_date belongs to
    added_at     TEXT NOT NULL,   -- ISO timestamp
    PRIMARY KEY (tmdb_id, media_type)
);

CREATE TABLE IF NOT EXISTS title_status (
    tmdb_id      INTEGER NOT NULL,
    media_type   TEXT NOT NULL,
    status       TEXT NOT NULL CHECK (status IN ('seen','not_interested','pending','watchlist')),
    decided_at   TEXT,
    source       TEXT,            -- 'seed' | 'weekly_run' | 'manual'
    rating       INTEGER CHECK (rating IS NULL OR rating BETWEEN 1 AND 5),
    PRIMARY KEY (tmdb_id, media_type),
    FOREIGN KEY (tmdb_id, media_type) REFERENCES titles(tmdb_id, media_type)
);

CREATE TABLE IF NOT EXISTS scores (
    tmdb_id      INTEGER NOT NULL,
    media_type   TEXT NOT NULL,
    cluster      TEXT NOT NULL,
    fit_score    INTEGER NOT NULL,
    why          TEXT NOT NULL,
    dealbreakers TEXT,
    scored_at    TEXT NOT NULL,
    profile_ver  INTEGER NOT NULL,
    PRIMARY KEY (tmdb_id, media_type)
);

CREATE TABLE IF NOT EXISTS profiles (
    version      INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_json TEXT NOT NULL,
    derived_at   TEXT NOT NULL,
    decision_count_at_derivation INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    run_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ran_at       TEXT NOT NULL,
    new_count    INTEGER,
    carryover_count INTEGER,
    api_calls_tmdb INTEGER,
    api_calls_claude INTEGER,
    notes        TEXT
);

-- Curated "Rate Your History" prompts: acclaimed/deeper-cut titles parked for
-- the user to rate. Separate from title_status so we don't have to rebuild the
-- status CHECK constraint. Rating one (via /decision) or skipping it clears the
-- row; unrated skips leave the title free to enter the normal rec pool.
CREATE TABLE IF NOT EXISTS rate_prompts (
    tmdb_id      INTEGER NOT NULL,
    media_type   TEXT NOT NULL,
    added_at     TEXT NOT NULL,
    PRIMARY KEY (tmdb_id, media_type),
    FOREIGN KEY (tmdb_id, media_type) REFERENCES titles(tmdb_id, media_type)
);

-- Subject/person/team vetoes: content ABOUT these is excluded regardless of
-- how well-made it is (orthogonal to fit_score). Matched case-insensitively
-- against title + overview + keywords, and fed to the scorer as a hard zero.
CREATE TABLE IF NOT EXISTS blocklist (
    term         TEXT PRIMARY KEY,   -- lowercased match string, e.g. 'muhammad ali'
    note         TEXT,               -- human label / reason
    added_at     TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# Columns added after initial release: CREATE TABLE IF NOT EXISTS is a no-op
# on an existing table, so new columns need an explicit ALTER TABLE migration.
_ADDED_COLUMNS = {
    "titles": {"release_date": "TEXT", "trailer_url": "TEXT",
               "original_language": "TEXT", "popularity": "REAL",
               "recent_date": "TEXT", "latest_season": "INTEGER"},
    "title_status": {"rating": "INTEGER"},
}


def _migrate(conn: sqlite3.Connection) -> None:
    for table, columns in _ADDED_COLUMNS.items():
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        for col, decl in columns.items():
            if col not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
                logger.info("migrated: added %s.%s", table, col)


def connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    path = db_path or config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()
    return conn


# --- titles -------------------------------------------------------------------

def upsert_title(conn: sqlite3.Connection, t: Dict) -> None:
    """Insert or refresh a title's metadata. added_at is preserved on update."""
    conn.execute(
        """INSERT INTO titles
               (tmdb_id, media_type, title, year, genres, keywords,
                poster_path, overview, tmdb_rating, release_date, trailer_url,
                original_language, popularity, recent_date, latest_season, added_at)
           VALUES (:tmdb_id, :media_type, :title, :year, :genres, :keywords,
                   :poster_path, :overview, :tmdb_rating, :release_date, :trailer_url,
                   :original_language, :popularity, :recent_date, :latest_season,
                   :added_at)
           ON CONFLICT (tmdb_id, media_type) DO UPDATE SET
               title=excluded.title, year=excluded.year, genres=excluded.genres,
               keywords=excluded.keywords, poster_path=excluded.poster_path,
               overview=excluded.overview, tmdb_rating=excluded.tmdb_rating,
               release_date=excluded.release_date, trailer_url=excluded.trailer_url,
               original_language=excluded.original_language,
               popularity=excluded.popularity, recent_date=excluded.recent_date,
               latest_season=excluded.latest_season""",
        {
            "tmdb_id": t["tmdb_id"], "media_type": t["media_type"],
            "title": t["title"], "year": t.get("year"),
            "genres": t.get("genres"), "keywords": t.get("keywords"),
            "poster_path": t.get("poster_path"), "overview": t.get("overview"),
            "tmdb_rating": t.get("tmdb_rating"), "release_date": t.get("release_date"),
            "trailer_url": t.get("trailer_url"),
            "original_language": t.get("original_language"),
            "popularity": t.get("popularity"), "recent_date": t.get("recent_date"),
            "latest_season": t.get("latest_season"), "added_at": now_iso(),
        },
    )


def title_exists(conn: sqlite3.Connection, tmdb_id: int, media_type: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM titles WHERE tmdb_id=? AND media_type=?", (tmdb_id, media_type)
    ).fetchone()
    return row is not None


# --- title_status ---------------------------------------------------------------

def set_status(
    conn: sqlite3.Connection,
    tmdb_id: int,
    media_type: str,
    status: str,
    source: str,
    decided: bool,
    rating: Optional[int] = None,
) -> None:
    """rating only applies to status='seen' (1-5 stars); ignored otherwise.
    Re-rating an already-'seen' title preserves the original decided_at rather
    than bumping it, since that's just a rating tweak, not a new decision."""
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid status: {status}")
    if status != "seen":
        rating = None
    existing = conn.execute(
        "SELECT status, decided_at FROM title_status WHERE tmdb_id=? AND media_type=?",
        (tmdb_id, media_type),
    ).fetchone()
    if existing and existing["status"] == status and existing["decided_at"] is not None:
        decided_at = existing["decided_at"]
    else:
        decided_at = now_iso() if decided else None
    conn.execute(
        """INSERT INTO title_status (tmdb_id, media_type, status, decided_at, source, rating)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT (tmdb_id, media_type) DO UPDATE SET
               status=excluded.status, decided_at=excluded.decided_at,
               source=excluded.source, rating=excluded.rating""",
        (tmdb_id, media_type, status, decided_at, source, rating),
    )


def known_keys(conn: sqlite3.Connection) -> Set[Tuple[int, str]]:
    """Every (tmdb_id, media_type) that must never re-enter the candidate pool:
    anything with a status (seen/rejected/pending/watchlist), a cached score
    (including sub-threshold titles that never got a status), or a live
    rate-history prompt (don't recommend what we're asking the user to rate)."""
    keys: Set[Tuple[int, str]] = set()
    for row in conn.execute("SELECT tmdb_id, media_type FROM title_status"):
        keys.add((row["tmdb_id"], row["media_type"]))
    for row in conn.execute("SELECT tmdb_id, media_type FROM scores"):
        keys.add((row["tmdb_id"], row["media_type"]))
    for row in conn.execute("SELECT tmdb_id, media_type FROM rate_prompts"):
        keys.add((row["tmdb_id"], row["media_type"]))
    return keys


def status_counts(conn: sqlite3.Connection) -> Dict[str, int]:
    return {
        row["status"]: row["n"]
        for row in conn.execute(
            "SELECT status, COUNT(*) AS n FROM title_status GROUP BY status"
        )
    }


# --- rate_prompts (Rate Your History) -----------------------------------------

def add_rate_prompt(conn: sqlite3.Connection, tmdb_id: int, media_type: str) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO rate_prompts (tmdb_id, media_type, added_at)
           VALUES (?, ?, ?)""",
        (tmdb_id, media_type, now_iso()),
    )


def clear_rate_prompt(conn: sqlite3.Connection, tmdb_id: int, media_type: str) -> None:
    conn.execute(
        "DELETE FROM rate_prompts WHERE tmdb_id=? AND media_type=?",
        (tmdb_id, media_type),
    )


def rate_prompt_keys(conn: sqlite3.Connection) -> Set[Tuple[int, str]]:
    return {
        (row["tmdb_id"], row["media_type"])
        for row in conn.execute("SELECT tmdb_id, media_type FROM rate_prompts")
    }


def rate_prompt_count(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) AS n FROM rate_prompts").fetchone()["n"])


# --- blocklist (subject/person/team vetoes) -----------------------------------

def add_blocklist_term(conn: sqlite3.Connection, term: str, note: Optional[str] = None) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO blocklist (term, note, added_at) VALUES (?, ?, ?)",
        (term.strip().lower(), note, now_iso()),
    )


def blocklist_terms(conn: sqlite3.Connection) -> List[str]:
    return [row["term"] for row in conn.execute("SELECT term FROM blocklist ORDER BY term")]


def blocklist_labels(conn: sqlite3.Connection) -> List[str]:
    """Human labels (note if present, else the term) for prompts/UI."""
    return [
        row["note"] or row["term"]
        for row in conn.execute("SELECT term, note FROM blocklist ORDER BY term")
    ]


def text_matches_blocklist(text: str, terms: List[str]) -> Optional[str]:
    """Return the first blocklist term found in text (case-insensitive), else None."""
    low = (text or "").lower()
    for term in terms:
        if term and term in low:
            return term
    return None


# --- scores ---------------------------------------------------------------------

def insert_score(conn: sqlite3.Connection, s: Dict) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO scores
               (tmdb_id, media_type, cluster, fit_score, why, dealbreakers,
                scored_at, profile_ver)
           VALUES (:tmdb_id, :media_type, :cluster, :fit_score, :why,
                   :dealbreakers, :scored_at, :profile_ver)""",
        s,
    )


def scored_keys(conn: sqlite3.Connection) -> Set[Tuple[int, str]]:
    return {
        (row["tmdb_id"], row["media_type"])
        for row in conn.execute("SELECT tmdb_id, media_type FROM scores")
    }


# --- profiles -------------------------------------------------------------------

def latest_profile(conn: sqlite3.Connection) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM profiles ORDER BY version DESC LIMIT 1"
    ).fetchone()


def insert_profile(conn: sqlite3.Connection, profile_json: str, decision_count: int) -> int:
    cur = conn.execute(
        """INSERT INTO profiles (profile_json, derived_at, decision_count_at_derivation)
           VALUES (?, ?, ?)""",
        (profile_json, now_iso(), decision_count),
    )
    return int(cur.lastrowid)


def count_manual_decisions_since(conn: sqlite3.Connection, since_iso: Optional[str]) -> int:
    """Manual seen/reject/watchlist actions since a timestamp (seed rows excluded)."""
    placeholders = ",".join("?" for _ in DECISION_STATUSES)
    sql = (
        f"SELECT COUNT(*) AS n FROM title_status "
        f"WHERE status IN ({placeholders}) AND source = 'manual' AND decided_at IS NOT NULL"
    )
    params: List = list(DECISION_STATUSES)
    if since_iso:
        sql += " AND decided_at > ?"
        params.append(since_iso)
    return int(conn.execute(sql, params).fetchone()["n"])


# --- runs -----------------------------------------------------------------------

def insert_run(
    conn: sqlite3.Connection,
    ran_at: str,
    new_count: int,
    carryover_count: int,
    api_calls_tmdb: int,
    api_calls_claude: int,
    notes: str,
) -> int:
    cur = conn.execute(
        """INSERT INTO runs (ran_at, new_count, carryover_count,
                             api_calls_tmdb, api_calls_claude, notes)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (ran_at, new_count, carryover_count, api_calls_tmdb, api_calls_claude, notes),
    )
    return int(cur.lastrowid)


def last_run(conn: sqlite3.Connection) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM runs ORDER BY run_id DESC LIMIT 1").fetchone()


def run_count(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) AS n FROM runs").fetchone()["n"])
