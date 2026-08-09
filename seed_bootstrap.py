"""One-time seed bootstrap: match seed titles to TMDB ids and mark them 'seen'.

Usage:
    python3 seed_bootstrap.py --dry-run       # print matches, write nothing
    python3 seed_bootstrap.py --interactive   # confirm ambiguous/low-confidence matches
    python3 seed_bootstrap.py                 # auto-insert confident matches, flag the rest

Matching: exact normalized title match wins; otherwise fuzzy ratio. Anything
below MIN_CONFIDENCE is flagged for manual review (or prompted in
--interactive) instead of silently inserted.
"""
import argparse
import difflib
import json
import logging
import re
import sys
from typing import Dict, List, Optional

import config
import db
from tmdb_client import TMDBClient

logger = logging.getLogger("seed_bootstrap")

MIN_CONFIDENCE = 0.6

# query: what we search TMDB for. media_type/year: optional disambiguation
# hints. note: why the hint exists.
SEEDS: List[Dict] = [
    {"query": "Maternal Instinct", "media_type": "movie", "year": 2026,
     "note": "2026 true-crime documentary — not the 2017 thriller"},
    {"query": "Worst Ex Ever", "media_type": "tv", "year": None, "note": "Netflix docuseries"},
    {"query": "The Murder of Rachel Nickell", "media_type": None, "year": None,
     "note": "true-crime doc"},
    {"query": "Peacemaker", "media_type": "tv", "year": 2022, "note": "HBO series, not the 1997 film"},
    {"query": "The Rainmaker", "media_type": "tv", "year": 2025, "note": "2025 series, not the 1997 film"},
    {"query": "The White Lotus", "media_type": "tv", "year": None, "note": None},
    {"query": "Bookie", "media_type": "tv", "year": None, "note": "Max comedy"},
    {"query": "Balls Up", "media_type": "movie", "year": 2026,
     "note": "2026 comedy — not the 1997 film"},
    {"query": "The Dynasty: New England Patriots", "media_type": "tv", "year": None,
     "note": "NFL/Patriots doc — NOT the Dynasty soap"},
    {"query": "Dark Side of the Ring", "media_type": "tv", "year": None, "note": None},
    {"query": "30 for 30", "media_type": "tv", "year": None, "note": "ESPN sports-doc anchor"},
    {"query": "The Righteous Gemstones", "media_type": "tv", "year": None, "note": None},
    {"query": "Aaron Rodgers: Enigma", "media_type": "tv", "year": None, "note": "Netflix doc"},
    {"query": "Mr. McMahon", "media_type": "tv", "year": None, "note": "Netflix doc"},
    {"query": "Euphoria", "media_type": "tv", "year": 2019, "note": "HBO — not the Israeli original"},
    {"query": "Severance", "media_type": "tv", "year": 2022, "note": None},
    {"query": "The Wire", "media_type": "tv", "year": 2002, "note": None},
    {"query": "Dexter", "media_type": "tv", "year": 2006, "note": "original series"},
    {"query": "Shameless", "media_type": "tv", "year": 2011, "note": "US version"},
    {"query": "Mad Men", "media_type": "tv", "year": None, "note": None},
    {"query": "Trust Me: The False Prophet", "media_type": "tv", "year": None,
     "note": "true-crime doc (seed list had it as 'The False Prophet')"},
    {"query": "Don't F**k with Cats", "media_type": "tv", "year": 2019,
     "note": "full title: Don't F**k with Cats: Hunting an Internet Killer"},
    {"query": "Sons of Anarchy", "media_type": "tv", "year": None, "note": None},
    {"query": "Peaky Blinders", "media_type": "tv", "year": 2013, "note": None},
]


def _norm(s: str) -> str:
    """Normalize for matching: lowercase, strip punctuation/asterisks, collapse spaces."""
    s = re.sub(r"[^a-z0-9 ]+", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def match_confidence(query: str, title: str) -> float:
    nq, nt = _norm(query), _norm(title)
    if not nq or not nt:
        return 0.0
    if nq == nt:
        return 1.0
    if nt.startswith(nq) or nq.startswith(nt):
        return 0.9
    return difflib.SequenceMatcher(None, nq, nt).ratio()


def find_candidates(client: TMDBClient, seed: Dict) -> List[Dict]:
    """Search the hinted media type (or both), score confidence, sort best-first.
    Exact matches beat fuzzy ones; popularity breaks ties (per spec)."""
    media_types = [seed["media_type"]] if seed["media_type"] else ["tv", "movie"]
    results: List[Dict] = []
    for mt in media_types:
        for r in client.search(seed["query"], mt, year=seed["year"]):
            r["confidence"] = match_confidence(seed["query"], r["title"])
            if seed["year"] and r["year"] and abs(r["year"] - seed["year"]) > 1:
                r["confidence"] *= 0.5
            results.append(r)
    results.sort(key=lambda r: (-r["confidence"], -r["popularity"]))
    return results


def is_ambiguous(candidates: List[Dict]) -> bool:
    """Two near-equal-confidence candidates from different titles/ids."""
    if len(candidates) < 2:
        return False
    a, b = candidates[0], candidates[1]
    return (
        a["confidence"] - b["confidence"] < 0.05
        and a["confidence"] >= MIN_CONFIDENCE
        and (a["tmdb_id"], a["media_type"]) != (b["tmdb_id"], b["media_type"])
    )


def prompt_choice(seed: Dict, candidates: List[Dict]) -> Optional[Dict]:
    print(f"\n  Ambiguous/low-confidence match for {seed['query']!r}"
          f" ({seed.get('note') or 'no note'}):")
    for i, c in enumerate(candidates[:5], 1):
        print(f"    [{i}] {c['title']} ({c['year']}, {c['media_type']}) "
              f"conf={c['confidence']:.2f} pop={c['popularity']:.0f}")
        if c["overview"]:
            print(f"        {c['overview'][:120]}")
    print("    [s] skip")
    choice = input("  pick: ").strip().lower()
    if choice.isdigit() and 1 <= int(choice) <= min(5, len(candidates)):
        return candidates[int(choice) - 1]
    return None


def insert_seed(conn, client: TMDBClient, match: Dict) -> None:
    detail = client.details(match["media_type"], match["tmdb_id"])
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
    db.set_status(conn, detail["tmdb_id"], detail["media_type"],
                  status="seen", source="seed", decided=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed the DB with already-seen titles")
    parser.add_argument("--dry-run", action="store_true", help="print matches, write nothing")
    parser.add_argument("--interactive", action="store_true",
                        help="prompt to confirm ambiguous/low-confidence matches")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    client = TMDBClient(config.get_tmdb_api_key())
    conn = db.connect()

    inserted, flagged, skipped = 0, 0, 0
    for seed in SEEDS:
        candidates = find_candidates(client, seed)
        if not candidates:
            logger.warning("NO MATCH: %r — flagged for manual review", seed["query"])
            flagged += 1
            continue
        best = candidates[0]
        needs_review = best["confidence"] < MIN_CONFIDENCE or is_ambiguous(candidates)
        if needs_review:
            if args.interactive and not args.dry_run:
                chosen = prompt_choice(seed, candidates)
                if chosen is None:
                    logger.info("skipped %r", seed["query"])
                    skipped += 1
                    continue
                best = chosen
            else:
                logger.warning(
                    "FLAGGED %r: best=%r (%s, %s) conf=%.2f — rerun with --interactive",
                    seed["query"], best["title"], best["year"], best["media_type"],
                    best["confidence"],
                )
                flagged += 1
                continue
        line = (f"{seed['query']!r} -> {best['title']!r} "
                f"({best['year']}, {best['media_type']}, id={best['tmdb_id']}, "
                f"conf={best['confidence']:.2f})")
        if args.dry_run:
            print(f"  [dry-run] {line}")
        else:
            if conn.execute(
                "SELECT 1 FROM title_status WHERE tmdb_id=? AND media_type=?",
                (best["tmdb_id"], best["media_type"]),
            ).fetchone():
                logger.info("already seeded, skipping: %s", line)
                continue
            insert_seed(conn, client, best)
            conn.commit()
            logger.info("seeded %s", line)
        inserted += 1

    logger.info("done: %d matched, %d flagged, %d skipped (tmdb calls: %d)",
                inserted, flagged, skipped, client.call_count)
    return 0 if flagged == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
