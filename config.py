"""Central configuration for theater-recommender.

All tunables live here. Secrets live only in .env (python-dotenv).
"""
import os
from pathlib import Path
from typing import Dict, List

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# --- Paths ------------------------------------------------------------------
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
LOG_DIR = OUTPUT_DIR / "logs"
DB_PATH = DATA_DIR / "recommender.db"
DASHBOARD_PATH = OUTPUT_DIR / "dashboard.html"

# --- Claude -----------------------------------------------------------------
# Scoring runs through the Claude Code CLI headless (-p) on the subscription —
# no ANTHROPIC_API_KEY, no metered API costs. See claude_cli.py.
CLAUDE_MODEL = "sonnet"         # CLI model alias
CLAUDE_CLI_TIMEOUT = 600        # seconds per CLI call
SCORER_CHUNK_SIZE = 20          # candidates per scoring call

# --- Pipeline tunables ------------------------------------------------------
POOL_CAP = 200                  # max novel titles per weekly run
SCORE_THRESHOLD = 40            # below this: never rendered, never re-scored
REDERIVE_INTERVAL = 20          # manual decisions between profile re-derivations
RECENCY_MONTHS = 240            # discover release-date window — era-agnostic per Dave
                                # (2026-07): best available from any year, not just new
RECENT_RELEASE_DAYS = 183       # "Recently Released" section window (~6 months);
                                # soft cut — older titles fall to Recommendations,
                                # nothing is dropped
ORIGINAL_LANGUAGE = "en"        # English-only: filters discover + anchor recommendations
ANCHORS_PER_RUN = 12            # seen/watchlist titles used for recommendation pulls
DISCOVER_PAGES = 5              # pages per discover query — deeper reach for novel
                                # series, since popular TV docs are mostly cached now
TMDB_SLEEP_SECONDS = 0.25       # politeness delay between TMDB calls
MOVIE_MAX_FRACTION = 0.25       # Dave watches series >> movies: cap movies at 25% of the pool

# --- Write-back server ------------------------------------------------------
SERVER_HOST = "127.0.0.1"
SERVER_PORT = 8757              # 8753 job-finder, 8754 house-hunter, 8756 birding-partner

# --- Clusters ---------------------------------------------------------------
# Canonical names: profile, scorer, and dashboard all key off these exact strings.
# Scripted drama/comedy clusters were dropped 2026-07-06 — nonfiction only now.
CLUSTER_TRUE_CRIME = "True Crime / Dark Nonfiction"
CLUSTER_SPORTS_DOCS = "Sports & Wrestling Docs"
# Reframed 2026-07-11 from generic "Other Nonfiction" (which fire-hosed
# music/nature/art/tech docs Dave has zero interest in) to his actual third
# love per his 5-star ratings: scams, cons, financial/corporate crime, cults.
CLUSTER_SCAMS = "Scams, Scandal & Corruption"

CLUSTER_NAMES: List[str] = [
    CLUSTER_TRUE_CRIME,
    CLUSTER_SPORTS_DOCS,
    CLUSTER_SCAMS,
]

CLUSTER_ACCENTS: Dict[str, str] = {
    CLUSTER_TRUE_CRIME: "#ff6b6b",
    CLUSTER_SPORTS_DOCS: "#ffb454",
    CLUSTER_SCAMS: "#5fd4c4",
}

# Discover queries per cluster. Keyword strings are resolved to TMDB keyword
# ids at runtime (never hardcode ids). Empty keywords = genre-only discover.
# Docs get a low vote floor since they get fewer votes than scripted content.
CLUSTERS: List[Dict] = [
    {
        "name": CLUSTER_TRUE_CRIME,
        "discover": [
            {"media_type": "tv", "with_genres": "99",
             "keywords": ["true crime", "serial killer", "murder", "missing person",
                          "manhunt", "kidnapping", "cold case", "mafia",
                          "drug cartel", "organized crime"], "vote_floor": 8},
            {"media_type": "movie", "with_genres": "99",
             "keywords": ["true crime", "serial killer", "murder", "missing person",
                          "manhunt", "kidnapping", "cold case", "mafia",
                          "drug cartel", "organized crime"], "vote_floor": 8},
        ],
    },
    {
        "name": CLUSTER_SPORTS_DOCS,
        "discover": [
            {"media_type": "tv", "with_genres": "99",
             "keywords": ["sports", "wrestling", "american football", "basketball",
                          "soccer", "ice hockey", "auto racing", "boxing",
                          "baseball"], "vote_floor": 10},
            {"media_type": "movie", "with_genres": "99",
             "keywords": ["sports", "wrestling", "american football", "basketball",
                          "soccer", "ice hockey", "auto racing", "boxing",
                          "baseball"], "vote_floor": 10},
        ],
    },
    {
        "name": CLUSTER_SCAMS,
        # Targeted keywords — NOT a keyword-less genre-99 firehose, which was
        # dragging in music/nature/art/tech docs that tanked fit.
        "discover": [
            {"media_type": "tv", "with_genres": "99",
             "keywords": ["fraud", "con artist", "scam", "ponzi scheme",
                          "financial crisis", "corruption", "cult", "scandal",
                          "white collar crime"], "vote_floor": 8},
            {"media_type": "movie", "with_genres": "99",
             "keywords": ["fraud", "con artist", "scam", "ponzi scheme",
                          "financial crisis", "corruption", "cult", "scandal",
                          "white collar crime"], "vote_floor": 8},
        ],
    },
]


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""


def get_tmdb_api_key() -> str:
    key = os.environ.get("TMDB_API_KEY", "").strip()
    if not key:
        raise ConfigError("TMDB_API_KEY not set — copy .env.example to .env and fill it in")
    return key
