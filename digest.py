"""Dashboard sections: classify recommendations for display.

Sections:
  recent          — pending, released within RECENT_RELEASE_DAYS of today
  recommendations — pending, everything else (older release or unknown date)
  watchlist       — explicitly flagged intent to watch (pinned)

Sub-threshold titles never render but stay cached so they are never
re-fetched or re-scored. Pending items never expire; rejects are human-only.
"""
import json
import logging
import sqlite3
from datetime import date, datetime, timezone
from typing import Dict, List, Optional

import config

logger = logging.getLogger(__name__)


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def is_recent_release(release_date: Optional[str], today: Optional[date] = None) -> bool:
    """True if release_date falls within the last RECENT_RELEASE_DAYS (and
    isn't dated in the future — an announced-but-unreleased title isn't 'out')."""
    released = _parse_date(release_date)
    if released is None:
        return False
    ref = today or datetime.now(timezone.utc).date()
    delta = (ref - released).days
    return 0 <= delta <= config.RECENT_RELEASE_DAYS


def days_since_release(release_date: Optional[str]) -> Optional[int]:
    released = _parse_date(release_date)
    if released is None:
        return None
    return max(0, (datetime.now(timezone.utc).date() - released).days)


def classify(
    status: Optional[str],
    fit_score: Optional[int],
    threshold: int,
    release_date: Optional[str],
) -> Optional[str]:
    """Pure section classifier. Returns 'recent'|'recommendations'|'watchlist'|None."""
    if status == "watchlist":
        return "watchlist"
    if status != "pending":
        return None  # seen / not_interested / no status: never rendered
    if fit_score is None or fit_score < threshold:
        return None
    return "recent" if is_recent_release(release_date) else "recommendations"


def weeks_ago(scored_at: Optional[str]) -> int:
    if not scored_at:
        return 0
    try:
        then = datetime.fromisoformat(scored_at)
    except ValueError:
        return 0
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return max(0, (datetime.now(timezone.utc) - then).days // 7)


def build_digest(conn: sqlite3.Connection, threshold: int) -> Dict:
    """Assemble dashboard sections from the DB. Sorted by fit desc, then age
    (older/less-recent first within the same score)."""
    rows = conn.execute(
        """SELECT t.tmdb_id, t.media_type, t.title, t.year, t.genres,
                  t.poster_path, t.overview, t.tmdb_rating, t.release_date,
                  t.trailer_url, ts.status, s.cluster, s.fit_score, s.why,
                  s.dealbreakers, s.scored_at
           FROM title_status ts
           JOIN titles t ON t.tmdb_id = ts.tmdb_id AND t.media_type = ts.media_type
           LEFT JOIN scores s ON s.tmdb_id = ts.tmdb_id AND s.media_type = ts.media_type
           WHERE ts.status IN ('pending','watchlist')"""
    ).fetchall()

    sections: Dict[str, List[Dict]] = {"recent": [], "recommendations": [], "watchlist": []}
    for r in rows:
        section = classify(r["status"], r["fit_score"], threshold, r["release_date"])
        if section is None:
            continue
        sections[section].append({
            "tmdb_id": r["tmdb_id"],
            "media_type": r["media_type"],
            "title": r["title"],
            "year": r["year"],
            "genres": json.loads(r["genres"]) if r["genres"] else [],
            "poster_path": r["poster_path"],
            "overview": r["overview"] or "",
            "tmdb_rating": r["tmdb_rating"],
            "trailer_url": r["trailer_url"],
            "cluster": r["cluster"] or "Unclustered",
            "fit_score": r["fit_score"] if r["fit_score"] is not None else 0,
            "why": r["why"] or "",
            "dealbreakers": r["dealbreakers"],
            "weeks_ago": weeks_ago(r["scored_at"]),
            "days_since_release": days_since_release(r["release_date"]),
        })

    for items in sections.values():
        items.sort(key=lambda x: (-x["fit_score"], -x["weeks_ago"]))

    rate_history = _rate_history_items(conn)

    return {
        "rate_history": rate_history,
        "recent": sections["recent"],
        "recommendations": sections["recommendations"],
        "watchlist": sections["watchlist"],
        "summary": {
            "rate_history_count": len(rate_history),
            "recent_count": len(sections["recent"]),
            "recommendations_count": len(sections["recommendations"]),
            "watchlist_count": len(sections["watchlist"]),
        },
    }


def _rate_history_items(conn: sqlite3.Connection) -> List[Dict]:
    """Curated 'Rate Your History' prompts still awaiting the user's input.
    A prompt whose title already has a decided status is skipped (already
    handled). Sorted by TMDB rating desc as an acclaim proxy."""
    rows = conn.execute(
        """SELECT t.tmdb_id, t.media_type, t.title, t.year, t.genres,
                  t.poster_path, t.overview, t.tmdb_rating, t.release_date,
                  t.trailer_url
           FROM rate_prompts rp
           JOIN titles t ON t.tmdb_id = rp.tmdb_id AND t.media_type = rp.media_type
           LEFT JOIN title_status ts
                  ON ts.tmdb_id = rp.tmdb_id AND ts.media_type = rp.media_type
           WHERE ts.status IS NULL
           ORDER BY (t.tmdb_rating IS NULL), t.tmdb_rating DESC"""
    ).fetchall()
    items = []
    for r in rows:
        items.append({
            "tmdb_id": r["tmdb_id"],
            "media_type": r["media_type"],
            "title": r["title"],
            "year": r["year"],
            "genres": json.loads(r["genres"]) if r["genres"] else [],
            "poster_path": r["poster_path"],
            "overview": r["overview"] or "",
            "tmdb_rating": r["tmdb_rating"],
            "trailer_url": r["trailer_url"],
        })
    return items
