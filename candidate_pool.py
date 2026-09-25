"""Weekly candidate pool: TMDB discover per cluster + recommendations from
rotating anchors, deduped against everything already known, capped with
per-cluster quotas so no cluster starves.

Standalone dry-run (no DB writes, no details calls):
    python3 candidate_pool.py --dry-run [--limit N]
"""
import argparse
import json
import logging
import sqlite3
from datetime import date, timedelta
from typing import Dict, List, Optional, Set, Tuple

import config
import db
from tmdb_client import TMDBClient, normalize_result

logger = logging.getLogger(__name__)

ANCHOR_BUCKET = "_anchor_similar"  # quota bucket for recommendation-sourced candidates


def dedupe_pool(candidates: List[Dict]) -> List[Dict]:
    """Drop duplicate (tmdb_id, media_type) pairs, keeping first occurrence."""
    seen: Set[Tuple[int, str]] = set()
    out: List[Dict] = []
    for c in candidates:
        key = (c["tmdb_id"], c["media_type"])
        if c["tmdb_id"] is None or key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def credits_to_candidates(credits: Dict, bucket: str) -> List[Dict]:
    """Turn a TMDB combined_credits payload into candidates: behind-the-camera
    (crew) work only — skips cameos and 'Self' appearances in docs — deduped
    across the several jobs one person often holds on the same title."""
    out: List[Dict] = []
    seen: Set[Tuple[int, str]] = set()
    for raw in credits.get("crew", []) or []:
        media_type = raw.get("media_type")
        if media_type not in ("movie", "tv") or raw.get("id") is None:
            continue
        key = (raw["id"], media_type)
        if key in seen:
            continue
        seen.add(key)
        c = normalize_result(raw, media_type)
        c["source_bucket"] = bucket
        out.append(c)
    return out


def drop_known(candidates: List[Dict], known: Set[Tuple[int, str]]) -> List[Dict]:
    """Drop anything with an existing status or cached score."""
    return [c for c in candidates if (c["tmdb_id"], c["media_type"]) not in known]


def drop_blocklisted(candidates: List[Dict], terms: List[str]) -> List[Dict]:
    """Drop candidates whose TITLE names a blocked subject/person/team (a title
    naming them means the doc is about them). Deliberately title-only, not
    overview, so a league-wide doc that merely mentions a blocked team survives
    — the scorer judges the subtler 'is it ABOUT them' cases and zeroes those."""
    if not terms:
        return candidates
    out: List[Dict] = []
    for c in candidates:
        hit = db.text_matches_blocklist(c.get("title", ""), terms)
        if hit:
            logger.info("blocklisted (%s): %s", hit, c.get("title"))
            continue
        out.append(c)
    return out


def apply_cap(candidates: List[Dict], cap: int) -> List[Dict]:
    """Trim to cap with per-bucket quotas: round-robin across source buckets,
    each bucket sorted by popularity desc, so no cluster starves."""
    if len(candidates) <= cap:
        return list(candidates)
    buckets: Dict[str, List[Dict]] = {}
    for c in candidates:
        buckets.setdefault(c.get("source_bucket", ANCHOR_BUCKET), []).append(c)
    for lst in buckets.values():
        lst.sort(key=lambda c: -c.get("popularity", 0.0))
    out: List[Dict] = []
    bucket_names = sorted(buckets)  # deterministic order
    i = 0
    while len(out) < cap and any(buckets[b] for b in bucket_names):
        name = bucket_names[i % len(bucket_names)]
        if buckets[name]:
            out.append(buckets[name].pop(0))
        i += 1
    return out


def pick_anchors(conn: sqlite3.Connection, per_run: int) -> List[sqlite3.Row]:
    """Rotating subset of the user's HIGH-SIGNAL titles to pull TMDB
    recommendations from: 4-5★ seen titles, watchlist items, and the original
    hand-picked seed list (unrated favorites like The Wire, Peaky Blinders —
    back in play now that scripted drama is a lane). Rotates by run count."""
    rows = conn.execute(
        """SELECT ts.tmdb_id, ts.media_type, t.title, ts.rating
           FROM title_status ts
           JOIN titles t ON t.tmdb_id = ts.tmdb_id AND t.media_type = ts.media_type
           WHERE ts.status = 'watchlist'
              OR (ts.status = 'seen' AND (ts.rating >= 4 OR ts.source = 'seed'))
           ORDER BY ts.rating DESC, ts.tmdb_id"""
    ).fetchall()
    if not rows:
        # Cold start / no ratings yet: fall back to any seen/watchlist.
        rows = conn.execute(
            """SELECT ts.tmdb_id, ts.media_type, t.title, ts.rating
               FROM title_status ts
               JOIN titles t ON t.tmdb_id = ts.tmdb_id AND t.media_type = ts.media_type
               WHERE ts.status IN ('seen','watchlist')
               ORDER BY ts.tmdb_id"""
        ).fetchall()
    if not rows:
        return []
    start = (db.run_count(conn) * per_run) % len(rows)
    return [rows[(start + i) % len(rows)] for i in range(min(per_run, len(rows)))]


def select_pool(candidates: List[Dict], cap: int, movie_max_fraction: float) -> List[Dict]:
    """Fill the pool series-first, keeping movies at/below movie_max_fraction of
    the ACTUAL pool. If novel series supply is short, the pool shrinks rather
    than padding with movies (Dave watches series >> films). If movies are
    short, the freed slots go to more series. Per-cluster quotas via apply_cap."""
    tv = [c for c in candidates if c["media_type"] == "tv"]
    mv = [c for c in candidates if c["media_type"] == "movie"]
    tv_slots = cap - int(cap * movie_max_fraction)
    movie_slots = cap - tv_slots
    chosen_tv = apply_cap(tv, tv_slots)
    chosen_mv = apply_cap(mv, movie_slots)
    if len(chosen_tv) < tv_slots:
        # Series short: cap movies so they stay <= fraction of the real total.
        max_movies = int(len(chosen_tv) * movie_max_fraction / (1 - movie_max_fraction))
        chosen_mv = apply_cap(mv, max_movies)
    elif len(chosen_mv) < movie_slots:
        # Movies short: hand the leftover slots to series.
        chosen_tv = apply_cap(tv, tv_slots + (movie_slots - len(chosen_mv)))
    return chosen_tv + chosen_mv


def _discover_cluster(tmdb: TMDBClient, cluster: Dict, date_gte: str) -> List[Dict]:
    out: List[Dict] = []
    for spec in cluster["discover"]:
        keyword_ids = [
            kid for kid in (tmdb.search_keyword_id(kw) for kw in spec["keywords"])
            if kid is not None
        ]
        for page in range(1, config.DISCOVER_PAGES + 1):
            results = tmdb.discover(
                media_type=spec["media_type"],
                with_genres=spec["with_genres"],
                keyword_ids=keyword_ids,
                vote_floor=spec["vote_floor"],
                date_gte=date_gte,
                page=page,
                with_networks=spec.get("with_networks", ""),
            )
            for r in results:
                r["source_bucket"] = cluster["name"]
            out.extend(results)

        # Popularity-sorted discover rarely surfaces brand-new releases (they
        # haven't accumulated votes yet), which starves the dashboard's
        # "Recently Released" section. A small newest-first pass with no vote
        # floor fixes that.
        newest_date_field = "first_air_date.desc" if spec["media_type"] == "tv" else "primary_release_date.desc"
        newest = tmdb.discover(
            media_type=spec["media_type"],
            with_genres=spec["with_genres"],
            keyword_ids=keyword_ids,
            vote_floor=0,
            date_gte=date_gte,
            page=1,
            sort_by=newest_date_field,
            with_networks=spec.get("with_networks", ""),
        )
        for r in newest:
            r["source_bucket"] = cluster["name"]
        out.extend(newest)
    return out


def generate_pool(
    conn: sqlite3.Connection,
    tmdb: TMDBClient,
    cap: int = config.POOL_CAP,
    dry_run: bool = False,
) -> Tuple[List[Dict], List[str]]:
    """Build this week's novel candidate list. Unless dry_run, fetches full
    details (genres/keywords) for each survivor and upserts into titles.
    Returns (pool, anchor titles used) — anchors go in the runs notes."""
    date_gte = (date.today() - timedelta(days=config.RECENCY_MONTHS * 30)).isoformat()
    raw: List[Dict] = []

    for cluster in config.CLUSTERS:
        found = _discover_cluster(tmdb, cluster, date_gte)
        logger.info("discover %-28s -> %d results", cluster["name"], len(found))
        raw.extend(found)

    anchors = pick_anchors(conn, config.ANCHORS_PER_RUN)
    anchor_titles = [a["title"] for a in anchors]
    logger.info("anchors this run: %s", anchor_titles)
    for a in anchors:
        recs = tmdb.recommendations(a["media_type"], a["tmdb_id"])
        for r in recs:
            r["source_bucket"] = ANCHOR_BUCKET
        raw.extend(recs)

    for person_id, name in config.BOOSTED_PEOPLE.items():
        credited = credits_to_candidates(tmdb.person_credits(person_id), config.CLUSTER_PRESTIGE)
        logger.info("boosted creator %s -> %d credits", name, len(credited))
        raw.extend(credited)

    # Discover is filtered server-side via with_original_language, but anchor
    # recommendations aren't — filter everything here (English-only).
    raw = [r for r in raw
           if r.get("original_language") in (None, config.ORIGINAL_LANGUAGE)]

    pool = dedupe_pool(raw)
    known = db.known_keys(conn)
    pool = drop_known(pool, known)
    novel = len(pool)
    pool = drop_blocklisted(pool, db.blocklist_terms(conn))
    after_block = len(pool)
    pool = select_pool(pool, cap, config.MOVIE_MAX_FRACTION)
    n_movies = sum(1 for c in pool if c["media_type"] == "movie")
    logger.info("pool: %d raw (en-only) -> %d novel -> %d after blocklist -> "
                "capped at %d (%d tv, %d movie)",
                len(raw), novel, after_block, len(pool), len(pool) - n_movies, n_movies)

    if dry_run:
        return pool, anchor_titles

    enriched: List[Dict] = []
    for c in pool:
        detail = tmdb.details(c["media_type"], c["tmdb_id"])
        detail["source_bucket"] = c.get("source_bucket", ANCHOR_BUCKET)
        db.upsert_title(conn, {
            "tmdb_id": detail["tmdb_id"],
            "media_type": detail["media_type"],
            "title": detail["title"],
            "year": detail["year"],
            "genres": json.dumps(detail["genre_names"]),
            "keywords": json.dumps(detail["keyword_names"]),
            "poster_path": detail["poster_path"],
            "overview": detail["overview"],
            "tmdb_rating": detail["tmdb_rating"],
            "release_date": detail["release_date"],
            "trailer_url": detail["trailer_url"],
            "original_language": detail["original_language"],
        })
        enriched.append(detail)
    conn.commit()
    return enriched, anchor_titles


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate/preview the weekly candidate pool")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the pool, skip details fetch and DB writes")
    parser.add_argument("--limit", type=int, default=None, help="cap pool size for testing")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    conn = db.connect()
    tmdb = TMDBClient(config.get_tmdb_api_key())
    pool, anchors = generate_pool(conn, tmdb, cap=args.limit or config.POOL_CAP,
                                  dry_run=args.dry_run)
    print(f"anchors: {', '.join(anchors) or '(none — run seed_bootstrap first)'}")
    for c in pool:
        print(f"  [{c.get('source_bucket', '?'):<28}] {c['title']} "
              f"({c['year']}, {c['media_type']}) pop={c.get('popularity', 0):.0f} "
              f"votes={c.get('vote_count', 0)}")
    print(f"\n{len(pool)} candidates, {tmdb.call_count} TMDB calls")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
