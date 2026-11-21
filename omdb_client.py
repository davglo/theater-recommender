"""IMDb rating + Rotten Tomatoes score via OMDb (free tier: 1,000 calls/day).

Key lives in .env as OMDB_API_KEY. Everything here is best-effort: a missing
key, a title OMDb doesn't know, or a network error just leaves the critic
columns empty and the card falls back to the TMDB rating.
"""
import logging
import os
import sqlite3
import time
from typing import Dict, Optional

import requests

import db
from tmdb_client import TMDBClient

logger = logging.getLogger(__name__)
URL = "https://www.omdbapi.com/"
RECHECK_DAYS = 7          # scores drift as votes come in; refresh weekly
DAILY_BUDGET = 900        # stay under the free tier's 1,000/day


def parse(data: Dict) -> Dict:
    """OMDb payload -> {imdb_rating, imdb_votes, rt_score}; missing = None."""
    def num(s: Optional[str], cast):
        try:
            return cast((s or "").replace(",", "").replace("%", ""))
        except ValueError:
            return None
    rt = next((r["Value"] for r in data.get("Ratings", [])
               if r.get("Source") == "Rotten Tomatoes"), None)
    return {"imdb_rating": num(data.get("imdbRating"), float),
            "imdb_votes": num(data.get("imdbVotes"), int),
            "rt_score": num(rt, int)}


def fetch(imdb_id: str, key: str) -> Optional[Dict]:
    try:
        data = requests.get(URL, params={"apikey": key, "i": imdb_id}, timeout=20).json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("OMDb failed for %s: %s", imdb_id, exc)
        return None
    if data.get("Response") != "True":
        return {"imdb_rating": None, "imdb_votes": None, "rt_score": None}
    return parse(data)


def enrich(conn: sqlite3.Connection, tmdb: TMDBClient, limit: int = DAILY_BUDGET) -> int:
    """Fill imdb_id (via TMDB external_ids) and critic scores for every active
    title that has none or is stale. Returns titles updated."""
    key = os.environ.get("OMDB_API_KEY", "").strip()
    if not key:
        logger.info("OMDB_API_KEY not set — skipping critic scores")
        return 0
    rows = conn.execute(
        """SELECT t.tmdb_id, t.media_type, t.imdb_id FROM titles t
           JOIN title_status ts ON ts.tmdb_id = t.tmdb_id AND ts.media_type = t.media_type
           WHERE ts.status IN ('pending','watchlist')
             AND (t.critics_checked_at IS NULL
                  OR t.critics_checked_at < date('now', ?))
           LIMIT ?""", (f"-{RECHECK_DAYS} days", limit)).fetchall()
    done = 0
    for r in rows:
        imdb_id = r["imdb_id"]
        if not imdb_id:
            try:
                imdb_id = tmdb._get(f"/{r['media_type']}/{r['tmdb_id']}/external_ids").get("imdb_id")
            except Exception as exc:  # noqa: BLE001 — one unknown title must not stop the batch
                logger.warning("no external ids for %s/%s: %s", r["media_type"], r["tmdb_id"], type(exc).__name__)
                imdb_id = None
        scores = fetch(imdb_id, key) if imdb_id else {"imdb_rating": None,
                                                      "imdb_votes": None, "rt_score": None}
        if scores is None:
            continue
        conn.execute(
            """UPDATE titles SET imdb_id=?, imdb_rating=?, imdb_votes=?, rt_score=?,
                      critics_checked_at=date('now') WHERE tmdb_id=? AND media_type=?""",
            (imdb_id, scores["imdb_rating"], scores["imdb_votes"], scores["rt_score"],
             r["tmdb_id"], r["media_type"]))
        done += 1
        if done % 25 == 0:
            conn.commit()
        time.sleep(0.1)
    conn.commit()
    logger.info("critic scores: %d titles updated", done)
    return done


if __name__ == "__main__":
    import config
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    enrich(db.connect(), TMDBClient(config.get_tmdb_api_key()))
