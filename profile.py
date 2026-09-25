"""Taste profile: static v1 from the seed list, periodic Claude re-derivation.

A profile is a JSON document with the four canonical clusters (names are fixed
— scorer and dashboard key off them), each with signals, anchors, a 0-1
weight, and free-form notes. Every REDERIVE_INTERVAL manual decisions, a
Claude call (via the Claude Code CLI — subscription, no API costs)
re-summarizes the profile from the full decision history. Cached scores from
older profile versions are never re-scored.
"""
import json
import logging
import sqlite3
from typing import Dict, List, Optional, Tuple

import claude_cli
import config
import db

logger = logging.getLogger(__name__)

INITIAL_PROFILE: Dict = {
    "clusters": [
        {
            "name": config.CLUSTER_TRUE_CRIME,
            "weight": 1.0,
            "anchors": ["Worst Ex Ever", "The Murder of Rachel Nickell",
                        "Don't F**k with Cats", "The False Prophet", "Maternal Instinct"],
            "signals": ["docuseries format", "investigative", "dark subject matter"],
            "notes": None,
        },
        {
            "name": config.CLUSTER_SPORTS_DOCS,
            "weight": 1.0,
            "anchors": ["Dark Side of the Ring", "Mr. McMahon", "30 for 30",
                        "Aaron Rodgers: Enigma", "The Dynasty: New England Patriots"],
            "signals": ["behind-the-curtain", "personality-driven",
                        "sports business/culture"],
            "notes": None,
        },
        {
            "name": config.CLUSTER_SCAMS,
            "weight": 1.0,
            "anchors": ["American Greed", "Dirty Money", "Enron: The Smartest Guys "
                        "in the Room", "Inside Job", "Bad Vegan", "Wild Wild Country"],
            "signals": ["financial & corporate fraud", "con artists and grifters",
                        "ponzi schemes / white-collar crime", "cults and cult leaders",
                        "corruption and scandal", "NOT music/nature/art/tech/general docs"],
            "notes": None,
        },
        {
            "name": config.CLUSTER_PRESTIGE,
            "weight": 1.2,
            "anchors": ["The Gentlemen", "MobLand", "Peaky Blinders", "Sons of Anarchy",
                        "The Wire", "Mad Men", "Severance", "The White Lotus",
                        "The Righteous Gemstones", "Peacemaker"],
            "signals": ["gangster / crime sagas, British crime capers",
                        "Guy Ritchie — anything he creates/directs/writes",
                        "morally complex antihero leads, serialized",
                        "dark / irreverent comedy, comedy-drama hybrids",
                        "NOT sitcoms, procedurals, romance, teen, family"],
            "notes": None,
        },
    ],
    "global_notes": "Three narrow documentary lanes (sports, true crime, "
                    "scams/fraud/cults — NOT music, nature, art, science/tech, "
                    "food/travel, or general-interest docs) plus one scripted "
                    "lane: prestige drama, crime/gangster series, and dark "
                    "comedy. Loves Guy Ritchie. Strong preference for SERIES "
                    "over films.",
}

REDERIVE_SYSTEM_PROMPT = (
    "You maintain a personal taste profile for a movie/TV recommender. "
    "Given the current profile and the user's full decision history, produce an "
    "updated profile as JSON with the EXACT same schema: an object with "
    "'clusters' (array of {name, weight, anchors, signals, notes}) and "
    "'global_notes'. Cluster names MUST remain exactly the same canonical "
    "names — capture emergent sub-preferences in signals/notes and shift the "
    "0-1 weights based on what was accepted vs rejected per cluster. "
    "'seen' decisions carry a 1-5 star rating (may be null for older/seed "
    "entries with no rating): treat 4-5 as strong positive signal, 3 as "
    "neutral/mild-positive, 1-2 as a soft negative (the title was tolerated, "
    "not liked) even though it's not a 'not_interested' rejection. Weight "
    "'not_interested' as the strongest negative signal. "
    "Output ONLY the JSON object. No markdown fences, no commentary. "
    "Respond directly from the information given — do not use tools, read "
    "files, or ask questions."
)


def ensure_initial_profile(conn: sqlite3.Connection) -> sqlite3.Row:
    """Insert profile v1 if the profiles table is empty; return latest profile."""
    row = db.latest_profile(conn)
    if row is not None:
        return row
    db.insert_profile(conn, json.dumps(INITIAL_PROFILE), decision_count=0)
    conn.commit()
    logger.info("inserted initial taste profile (v1)")
    latest = db.latest_profile(conn)
    assert latest is not None
    return latest


def rederive_due(conn: sqlite3.Connection) -> Tuple[bool, int]:
    """(due?, decisions since last derivation)."""
    latest = db.latest_profile(conn)
    since = latest["derived_at"] if latest else None
    n = db.count_manual_decisions_since(conn, since)
    return n >= config.REDERIVE_INTERVAL, n


def _decision_history(conn: sqlite3.Connection) -> List[Dict]:
    rows = conn.execute(
        """SELECT t.title, t.year, t.media_type, t.genres, ts.status, ts.source,
                  ts.rating, s.cluster, s.fit_score
           FROM title_status ts
           JOIN titles t ON t.tmdb_id = ts.tmdb_id AND t.media_type = ts.media_type
           LEFT JOIN scores s ON s.tmdb_id = ts.tmdb_id AND s.media_type = ts.media_type
           WHERE ts.status IN ('seen','not_interested','watchlist')
           ORDER BY ts.decided_at"""
    ).fetchall()
    history = []
    for r in rows:
        history.append({
            "title": r["title"], "year": r["year"], "media_type": r["media_type"],
            "genres": json.loads(r["genres"]) if r["genres"] else [],
            "decision": r["status"], "rating": r["rating"], "source": r["source"],
            "cluster": r["cluster"], "fit_score": r["fit_score"],
        })
    return history


def parse_profile_json(text: str) -> Optional[Dict]:
    """Defensive parse of a Claude profile response. Returns None if unusable."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError:
        return None
    clusters = parsed.get("clusters")
    if not isinstance(clusters, list) or not clusters:
        return None
    names = {c.get("name") for c in clusters if isinstance(c, dict)}
    if names != set(config.CLUSTER_NAMES):
        logger.warning("re-derived profile has wrong cluster names: %s", names)
        return None
    return parsed


def rederive_profile(
    conn: sqlite3.Connection,
    dry_run: bool = False,
) -> Optional[int]:
    """Re-derive the profile from full decision history. Returns the new
    version number, or None if skipped/failed (old profile stays active)."""
    latest = db.latest_profile(conn)
    current = json.loads(latest["profile_json"]) if latest else INITIAL_PROFILE
    history = _decision_history(conn)
    user_msg = (
        f"Current profile:\n{json.dumps(current, indent=1)}\n\n"
        f"Decision history ({len(history)} items, chronological; source='seed' "
        f"means pre-recommender viewing history):\n{json.dumps(history, indent=1)}"
    )
    if dry_run:
        print("[dry-run] profile re-derivation prompt:\n")
        print(REDERIVE_SYSTEM_PROMPT + "\n\n" + user_msg)
        return None

    try:
        text = claude_cli.call(REDERIVE_SYSTEM_PROMPT, user_msg)
    except claude_cli.ClaudeCLIError as exc:
        logger.error("profile re-derivation call failed: %s", exc)
        return None
    parsed = parse_profile_json(text)
    if parsed is None:
        logger.error("could not parse re-derived profile; keeping old version")
        return None
    n_decisions = db.count_manual_decisions_since(conn, None)
    version = db.insert_profile(conn, json.dumps(parsed), n_decisions)
    conn.commit()
    logger.info("profile re-derived: v%d (%d total decisions)", version, n_decisions)
    return version
