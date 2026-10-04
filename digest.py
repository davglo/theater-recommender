"""Dashboard sections — a RECENT-RELEASES board (rebuilt 2026-10-04).

Sections:
  recent    — pending, released in the last RECENT_RELEASE_DAYS (for TV: the
              latest season premiered in that window), ranked by fit + buzz
  older     — "Older Gems": the top OLDER_GEMS_CAP older pending recs by fit
  watchlist — explicitly flagged intent to watch (pinned)

Buzz = a 0..BUZZ_MAX bonus from TMDB popularity, ranked WITHIN each lane so a
hot true-crime doc competes on equal footing with a hot scripted drama.
Sub-threshold titles never render but stay cached (never re-scored).
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


def is_recent_release(recent_date: Optional[str], today: Optional[date] = None) -> bool:
    """True if recent_date falls within the last RECENT_RELEASE_DAYS (and isn't
    in the future — an announced-but-unreleased title isn't 'out')."""
    released = _parse_date(recent_date)
    if released is None:
        return False
    ref = today or datetime.now(timezone.utc).date()
    return 0 <= (ref - released).days <= config.RECENT_RELEASE_DAYS


def days_since_release(recent_date: Optional[str]) -> Optional[int]:
    released = _parse_date(recent_date)
    if released is None:
        return None
    return max(0, (datetime.now(timezone.utc).date() - released).days)


def recency_label(media_type: str, latest_season: Optional[int], days: Optional[int]) -> str:
    """'New season 2 · 5 days ago' / 'New series · 3 wks ago' / 'Released today'."""
    if days is None:
        ago = ""
    elif days == 0:
        ago = "today"
    elif days < 14:
        ago = f"{days} day{'s' if days > 1 else ''} ago"
    else:
        ago = f"{days // 7} wks ago"
    if media_type == "tv":
        kind = f"New season {latest_season}" if latest_season and latest_season > 1 else "New series"
    else:
        kind = "Released"
    return f"{kind} · {ago}" if ago and kind != "Released" else f"{kind} {ago}".strip()


def classify(
    status: Optional[str],
    fit_score: Optional[int],
    threshold: int,
    recent_date: Optional[str],
) -> Optional[str]:
    """Pure section classifier. Returns 'recent'|'older'|'watchlist'|None."""
    if status == "watchlist":
        return "watchlist"
    if status != "pending":
        return None  # seen / not_interested / no status: never rendered
    if fit_score is None or fit_score < threshold:
        return None
    return "recent" if is_recent_release(recent_date) else "older"


def apply_buzz(items: List[Dict]) -> None:
    """Set item['buzz'] (0..BUZZ_MAX), item['hot'] and item['rank_score'] =
    fit + buzz. Buzz is the item's popularity percentile WITHIN its cluster,
    so lanes with naturally low TMDB popularity (docs) aren't buried."""
    by_cluster: Dict[str, List[Dict]] = {}
    for it in items:
        by_cluster.setdefault(it["cluster"], []).append(it)
    for peers in by_cluster.values():
        pops = [p.get("popularity") or 0.0 for p in peers]
        for it in peers:
            pop = it.get("popularity") or 0.0
            pct = 0.5 if len(peers) == 1 else sum(1 for x in pops if x < pop) / (len(peers) - 1)
            it["buzz"] = round(config.BUZZ_MAX * pct)
            it["hot"] = it["buzz"] >= round(config.BUZZ_MAX * 0.75)
            it["rank_score"] = it["fit_score"] + it["buzz"]


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
    """Assemble dashboard sections from the DB (see module docstring)."""
    rows = conn.execute(
        """SELECT t.tmdb_id, t.media_type, t.title, t.year, t.genres,
                  t.poster_path, t.overview, t.tmdb_rating, t.trailer_url,
                  COALESCE(t.recent_date, t.release_date) AS recent_date,
                  t.latest_season, t.popularity, ts.status, s.cluster,
                  s.fit_score, s.why, s.dealbreakers, s.scored_at
           FROM title_status ts
           JOIN titles t ON t.tmdb_id = ts.tmdb_id AND t.media_type = ts.media_type
           LEFT JOIN scores s ON s.tmdb_id = ts.tmdb_id AND s.media_type = ts.media_type
           WHERE ts.status IN ('pending','watchlist')"""
    ).fetchall()

    sections: Dict[str, List[Dict]] = {"recent": [], "older": [], "watchlist": []}
    for r in rows:
        section = classify(r["status"], r["fit_score"], threshold, r["recent_date"])
        if section is None:
            continue
        days = days_since_release(r["recent_date"])
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
            "popularity": r["popularity"],
            "weeks_ago": weeks_ago(r["scored_at"]),
            "days_since_release": days,
            "recency_label": recency_label(r["media_type"], r["latest_season"], days),
        })

    apply_buzz(sections["recent"])
    sections["recent"].sort(key=lambda x: (-x["rank_score"], -x["fit_score"]))
    sections["older"].sort(key=lambda x: -x["fit_score"])
    older_total = len(sections["older"])
    sections["older"] = sections["older"][:config.OLDER_GEMS_CAP]
    sections["watchlist"].sort(key=lambda x: -x["fit_score"])

    rate_history = _rate_history_items(conn)

    return {
        "rate_history": rate_history,
        "recent": sections["recent"],
        "older": sections["older"],
        "watchlist": sections["watchlist"],
        "summary": {
            "rate_history_count": len(rate_history),
            "recent_count": len(sections["recent"]),
            "older_count": len(sections["older"]),
            "older_total": older_total,
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
