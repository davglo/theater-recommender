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
# Read-only snapshot published to GitHub Pages (committed; served at
# https://<user>.github.io/theater-recommender/). Refreshed on each sync.
DOCS_DIR = BASE_DIR / "docs"
DOCS_INDEX = DOCS_DIR / "index.html"

# --- Claude -----------------------------------------------------------------
# Scoring runs through the Claude Code CLI headless (-p) on the subscription —
# no ANTHROPIC_API_KEY, no metered API costs. See claude_cli.py.
CLAUDE_MODEL = "sonnet"         # CLI model alias
CLAUDE_CLI_TIMEOUT = 600        # seconds per CLI call
SCORER_CHUNK_SIZE = 20          # candidates per scoring call

# --- Pipeline tunables ------------------------------------------------------
POOL_CAP = 200                  # max novel titles per weekly run
SCORE_THRESHOLD = 40            # below this: never rendered, never re-scored
# OFF since 2026-10-04 at Dave's request ("get rid of my scoring for now,
# prioritize recency"): no Claude calls at all — every recent title that passes
# the lane/English/blocklist filters goes on the board, newest first, and the
# Nope button is the filter. Flip to True to restore fit scoring, profile
# re-derivation, trending/anchor sourcing, buzz ranking and Older Gems.
# (Re-enabling: unscored pending titles need scoring — resume_scoring.py skips
# titles that already have a status, so adjust it first.)
FIT_SCORING = False
REDERIVE_INTERVAL = 20          # manual decisions between profile re-derivations
# Recent-releases-only board (2026-10-04): sourcing, filtering and the main
# section all key off this window. A TV show counts if a season PREMIERED in it.
RECENT_RELEASE_DAYS = 92        # ~3 months
OLDER_GEMS_CAP = 10             # older high-fit pending recs shown below the new releases
BUZZ_MAX = 12                   # max rank bonus for trending, relative to lane peers
TRENDING_PAGES = 2              # pages of TMDB weekly trending per media type
ORIGINAL_LANGUAGE = "en"        # English-only: filters discover + anchor recommendations
ANCHORS_PER_RUN = 12            # seen/watchlist titles used for recommendation pulls
DISCOVER_PAGES = 5              # pages per discover query — deeper reach for novel
                                # series, since popular TV docs are mostly cached now
TMDB_SLEEP_SECONDS = 0.25       # politeness delay between TMDB calls
MOVIE_MAX_FRACTION = 0.25       # Dave watches series >> movies: cap movies at 25% of the pool

# --- Write-back server ------------------------------------------------------
SERVER_HOST = "127.0.0.1"      # advertised host (display only)
SERVER_PORT = 8757             # 8753 job-finder, 8754 house-hunter, 8756 birding-partner
# Bind address for the write-back server. "0.0.0.0" lets other devices on your
# network (or Tailscale) reach the dashboard — needed for phone access. Set
# back to "127.0.0.1" to lock it to this Mac only. No auth, so only expose it
# on networks you trust (home WiFi, Tailscale).
BIND_HOST = "0.0.0.0"

# --- Clusters ---------------------------------------------------------------
# Canonical names: profile, scorer, and dashboard all key off these exact strings.
# Scripted was dropped 2026-07-06, then brought back 2026-09-25 as one broad
# lane after Dave loved The Gentlemen + MobLand ("bring back all prestige drama").
CLUSTER_TRUE_CRIME = "True Crime / Dark Nonfiction"
CLUSTER_SPORTS_DOCS = "Sports & Wrestling Docs"
# Reframed 2026-07-11 from generic "Other Nonfiction" (which fire-hosed
# music/nature/art/tech docs Dave has zero interest in) to his actual third
# love per his 5-star ratings: scams, cons, financial/corporate crime, cults.
CLUSTER_SCAMS = "Scams, Scandal & Corruption"
CLUSTER_PRESTIGE = "Prestige Drama & Crime Series"  # scripted: crime/gangster, prestige, dark comedy

CLUSTER_NAMES: List[str] = [
    CLUSTER_TRUE_CRIME,
    CLUSTER_SPORTS_DOCS,
    CLUSTER_SCAMS,
    CLUSTER_PRESTIGE,
]

CLUSTER_ACCENTS: Dict[str, str] = {
    CLUSTER_TRUE_CRIME: "#ff6b6b",
    CLUSTER_SPORTS_DOCS: "#ffb454",
    CLUSTER_SCAMS: "#5fd4c4",
    CLUSTER_PRESTIGE: "#4aa8ff",
}

# Creators Dave loves: their full TMDB credits are pulled into every pool, and
# the scorer gives their work a bonus. {tmdb_person_id: name}
BOOSTED_PEOPLE: Dict[int, str] = {956: "Guy Ritchie"}

# HBO, Netflix, FX, AMC, Showtime, Starz, Apple TV, Prime Video, Hulu,
# Paramount+, HBO Max, Sky Atlantic, Peacock, BBC One, BBC Two, Channel 4.
PREMIUM_NETWORKS = "|".join(str(n) for n in (
    49, 213, 88, 174, 67, 318, 2552, 1024, 453, 4330, 3186, 1063, 3353, 4, 332, 26))

# "Junk drama" genres excluded from the scripted lane (Dave, 2026-10-04) —
# verified against TMDB /genre/{tv,movie}/list. Doc lanes are NOT filtered:
# TMDB tags some true-crime docuseries "Reality".
JUNK_TV_GENRES = "10751,10762,10763,10764,10766,10767"   # Family, Kids, News, Reality, Soap, Talk
JUNK_MOVIE_GENRES = "10749,10751,10770"                   # Romance, Family, TV Movie (Lifetime-style)

# Discover queries per cluster. Keyword strings are resolved to TMDB keyword
# ids at runtime (never hardcode ids). Empty keywords = genre-only discover.
# Vote floors are low on purpose: titles from the last ~3 months haven't had
# time to collect votes (quality comes from the network filter + the scorer).
CLUSTERS: List[Dict] = [
    {
        "name": CLUSTER_TRUE_CRIME,
        "discover": [
            {"media_type": "tv", "with_genres": "99",
             "keywords": ["true crime", "serial killer", "murder", "missing person",
                          "manhunt", "kidnapping", "cold case", "mafia",
                          "drug cartel", "organized crime"], "vote_floor": 2},
            {"media_type": "movie", "with_genres": "99",
             "keywords": ["true crime", "serial killer", "murder", "missing person",
                          "manhunt", "kidnapping", "cold case", "mafia",
                          "drug cartel", "organized crime"], "vote_floor": 2},
        ],
    },
    {
        "name": CLUSTER_SPORTS_DOCS,
        "discover": [
            {"media_type": "tv", "with_genres": "99",
             "keywords": ["sports", "wrestling", "american football", "basketball",
                          "soccer", "ice hockey", "auto racing", "boxing",
                          "baseball"], "vote_floor": 2},
            {"media_type": "movie", "with_genres": "99",
             "keywords": ["sports", "wrestling", "american football", "basketball",
                          "soccer", "ice hockey", "auto racing", "boxing",
                          "baseball"], "vote_floor": 2},
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
                          "white collar crime"], "vote_floor": 2},
            {"media_type": "movie", "with_genres": "99",
             "keywords": ["fraud", "con artist", "scam", "ponzi scheme",
                          "financial crisis", "corruption", "cult", "scandal",
                          "white collar crime"], "vote_floor": 2},
        ],
    },
    {
        "name": CLUSTER_PRESTIGE,
        # Scripted, so vote floors are much higher than for docs to keep
        # quality up. Dark comedy mostly arrives via anchors (Gemstones,
        # Peacemaker, The Gentlemen) rather than a noisy genre-35 firehose.
        # TV is restricted to premium/streaming networks: a genre-only pull
        # otherwise fills up with network procedurals (Chicago Fire, Blue
        # Bloods, Castle). IDs verified against TMDB /network/{id}.
        "discover": [
            {"media_type": "tv", "with_genres": "80", "keywords": [], "vote_floor": 5,
             "with_networks": PREMIUM_NETWORKS, "without_genres": JUNK_TV_GENRES},
            {"media_type": "tv", "with_genres": "18", "keywords": [], "vote_floor": 5,
             "with_networks": PREMIUM_NETWORKS, "without_genres": JUNK_TV_GENRES},
            {"media_type": "movie", "with_genres": "80", "keywords": [], "vote_floor": 20,
             "without_genres": JUNK_MOVIE_GENRES},
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
