"""Populate the 'Rate Your History' wall with a curated list of acclaimed +
deeper-cut nonfiction titles across the user's clusters. The user rates the
ones they've seen (fast taste signal) and skips the rest.

Matching mirrors seed_bootstrap: search TMDB, pick the best confident match,
skip anything already in the pipeline (has a title_status row). English only.

    python3 rate_history_seed.py --dry-run   # preview matches, no writes
    python3 rate_history_seed.py             # insert prompts
"""
import argparse
import logging
import sys
from typing import Dict, List, Optional

import config
import db
from seed_bootstrap import find_candidates, MIN_CONFIDENCE
from tmdb_client import TMDBClient, detail_to_title_row

logger = logging.getLogger("rate_history_seed")

# Curated across clusters: true crime / scams / mob / cults, all-sports docs,
# and music/celebrity + dark general nonfiction. Canon plus deeper cuts.
CURATED: List[Dict] = [
    # --- True crime / dark nonfiction ---
    {"query": "The Jinx: The Life and Deaths of Robert Durst", "media_type": "tv", "year": 2015},
    {"query": "Making a Murderer", "media_type": "tv", "year": 2015},
    {"query": "Wild Wild Country", "media_type": "tv", "year": 2018},
    {"query": "Tiger King", "media_type": "tv", "year": 2020},
    {"query": "The Keepers", "media_type": "tv", "year": 2017},
    {"query": "Evil Genius", "media_type": "tv", "year": 2018},
    {"query": "I'll Be Gone in the Dark", "media_type": "tv", "year": 2020},
    {"query": "Abducted in Plain Sight", "media_type": "movie", "year": 2017},
    {"query": "The Act of Killing", "media_type": "movie", "year": 2012},
    {"query": "Dear Zachary", "media_type": "movie", "year": 2008},
    {"query": "The Imposter", "media_type": "movie", "year": 2012},
    {"query": "Amanda Knox", "media_type": "movie", "year": 2016},
    {"query": "The Central Park Five", "media_type": "movie", "year": 2012},
    {"query": "Cropsey", "media_type": "movie", "year": 2009},
    {"query": "Casting JonBenet", "media_type": "movie", "year": 2017},
    # --- Cults & extremism ---
    {"query": "The Vow", "media_type": "tv", "year": 2020},
    {"query": "Going Clear: Scientology and the Prison of Belief", "media_type": "movie", "year": 2015},
    {"query": "Holy Hell", "media_type": "movie", "year": 2016},
    # --- Scams / fraud ---
    {"query": "Fyre", "media_type": "movie", "year": 2019},
    {"query": "The Inventor: Out for Blood in Silicon Valley", "media_type": "movie", "year": 2019},
    {"query": "LuLaRich", "media_type": "tv", "year": 2021},
    {"query": "Bad Vegan: Fame. Fraud. Fugitives.", "media_type": "tv", "year": 2022},
    {"query": "The Tinder Swindler", "media_type": "movie", "year": 2022},
    {"query": "Enron: The Smartest Guys in the Room", "media_type": "movie", "year": 2005},
    {"query": "McMillions", "media_type": "tv", "year": 2020},
    {"query": "Generation Hustle", "media_type": "tv", "year": 2021},
    # --- Sports (all sports) ---
    {"query": "The Last Dance", "media_type": "tv", "year": 2020},
    {"query": "Senna", "media_type": "movie", "year": 2010},
    {"query": "Free Solo", "media_type": "movie", "year": 2018},
    {"query": "Icarus", "media_type": "movie", "year": 2017},
    {"query": "Hoop Dreams", "media_type": "movie", "year": 1994},
    {"query": "When We Were Kings", "media_type": "movie", "year": 1996},
    {"query": "Diego Maradona", "media_type": "movie", "year": 2019},
    {"query": "Sunderland 'Til I Die", "media_type": "tv", "year": 2018},
    {"query": "Formula 1: Drive to Survive", "media_type": "tv", "year": 2019},
    {"query": "Untold", "media_type": "tv", "year": 2021},
    {"query": "The Two Escobars", "media_type": "movie", "year": 2010},
    {"query": "Andre the Giant", "media_type": "movie", "year": 2018},
    {"query": "Beckham", "media_type": "tv", "year": 2023},
    {"query": "Pumping Iron", "media_type": "movie", "year": 1977},
    # --- Music / celebrity & dark pop culture ---
    {"query": "Amy", "media_type": "movie", "year": 2015},
    {"query": "Framing Britney Spears", "media_type": "movie", "year": 2021},
    {"query": "Leaving Neverland", "media_type": "movie", "year": 2019},
    {"query": "Surviving R. Kelly", "media_type": "tv", "year": 2019},
    {"query": "Kurt Cobain: Montage of Heck", "media_type": "movie", "year": 2015},
    {"query": "What Happened, Miss Simone?", "media_type": "movie", "year": 2015},
    {"query": "The Andy Warhol Diaries", "media_type": "tv", "year": 2022},
    # --- Dark general nonfiction ---
    {"query": "Blackfish", "media_type": "movie", "year": 2013},
    {"query": "The Cove", "media_type": "movie", "year": 2009},
    {"query": "Grizzly Man", "media_type": "movie", "year": 2005},
    {"query": "13th", "media_type": "movie", "year": 2016},
    {"query": "Citizenfour", "media_type": "movie", "year": 2014},
    {"query": "Man on Wire", "media_type": "movie", "year": 2008},
    {"query": "American Factory", "media_type": "movie", "year": 2019},

    # --- Popular / widely-watched (mainstream — the stuff most people have seen) ---
    # True crime
    {"query": "Conversations with a Killer: The Ted Bundy Tapes", "media_type": "tv", "year": 2019},
    {"query": "Night Stalker: The Hunt for a Serial Killer", "media_type": "tv", "year": 2021},
    {"query": "American Murder: The Family Next Door", "media_type": "movie", "year": 2020},
    {"query": "The Girl in the Picture", "media_type": "movie", "year": 2022},
    {"query": "Our Father", "media_type": "movie", "year": 2022},
    {"query": "The Menendez Brothers", "media_type": "movie", "year": 2024},
    {"query": "American Nightmare", "media_type": "tv", "year": 2024},
    {"query": "Waco: American Apocalypse", "media_type": "tv", "year": 2023},
    {"query": "Sins of Our Mother", "media_type": "tv", "year": 2022},
    # Cults
    {"query": "Keep Sweet: Pray and Obey", "media_type": "tv", "year": 2022},
    {"query": "Escaping Twin Flames", "media_type": "tv", "year": 2023},
    {"query": "Seduced: Inside the NXIVM Cult", "media_type": "tv", "year": 2020},
    # Scams
    {"query": "Pepsi, Where's My Jet?", "media_type": "tv", "year": 2022},
    # Sports (popular, all sports)
    {"query": "Quarterback", "media_type": "tv", "year": 2023},
    {"query": "Receiver", "media_type": "tv", "year": 2024},
    {"query": "Full Swing", "media_type": "tv", "year": 2023},
    {"query": "Break Point", "media_type": "tv", "year": 2023},
    {"query": "Arnold", "media_type": "tv", "year": 2023},
    {"query": "Welcome to Wrexham", "media_type": "tv", "year": 2022},
    {"query": "The Redeem Team", "media_type": "movie", "year": 2022},
    {"query": "Court of Gold", "media_type": "tv", "year": 2025},
    {"query": "Sprint", "media_type": "tv", "year": 2024},
    {"query": "Tour de France: Unchained", "media_type": "tv", "year": 2023},
    {"query": "Simone Biles Rising", "media_type": "tv", "year": 2024},
    # Music / celebrity
    {"query": "Miss Americana", "media_type": "movie", "year": 2020},
    {"query": "Pamela, A Love Story", "media_type": "movie", "year": 2023},
    {"query": "Robbie Williams", "media_type": "tv", "year": 2023},
    {"query": "jeen-yuhs: A Kanye Trilogy", "media_type": "tv", "year": 2022},
    {"query": "Moonage Daydream", "media_type": "movie", "year": 2022},
    {"query": "The Beatles: Get Back", "media_type": "tv", "year": 2021},
    {"query": "Homecoming: A Film by Beyoncé", "media_type": "movie", "year": 2019},
    {"query": "This Is Paris", "media_type": "movie", "year": 2020},
    # Dark general / pop-culture
    {"query": "The Social Dilemma", "media_type": "movie", "year": 2020},
    {"query": "Class Action Park", "media_type": "movie", "year": 2020},
    {"query": "Three Identical Strangers", "media_type": "movie", "year": 2018},
    {"query": "Seaspiracy", "media_type": "movie", "year": 2021},
    {"query": "Val", "media_type": "movie", "year": 2021},
]


def best_match(client: TMDBClient, seed: Dict) -> Optional[Dict]:
    candidates = find_candidates(client, {
        "query": seed["query"], "media_type": seed["media_type"], "year": seed["year"],
        "note": None,
    })
    if not candidates:
        return None
    best = candidates[0]
    return best if best["confidence"] >= MIN_CONFIDENCE else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed the Rate Your History wall")
    parser.add_argument("--dry-run", action="store_true", help="preview, no writes")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    client = TMDBClient(config.get_tmdb_api_key())
    conn = db.connect()
    blocked = db.blocklist_terms(conn)

    added, skipped_known, unmatched, non_english, blocked_ct = 0, 0, 0, 0, 0
    for seed in CURATED:
        match = best_match(client, seed)
        if match is None:
            logger.warning("NO confident match: %r", seed["query"])
            unmatched += 1
            continue
        hay = f"{match['title']} {match.get('overview', '')}"
        hit = db.text_matches_blocklist(hay, blocked)
        if hit:
            logger.info("blocklisted (%s), skipping: %r", hit, match["title"])
            blocked_ct += 1
            continue
        key = (match["tmdb_id"], match["media_type"])
        # Skip anything already in the pipeline (seen/pending/rejected/watchlist).
        if conn.execute(
            "SELECT 1 FROM title_status WHERE tmdb_id=? AND media_type=?", key
        ).fetchone():
            logger.info("already in pipeline, skipping: %r", match["title"])
            skipped_known += 1
            continue
        detail = client.details(match["media_type"], match["tmdb_id"])
        if detail.get("original_language") not in (None, config.ORIGINAL_LANGUAGE):
            logger.info("non-English (%s), skipping: %r",
                        detail.get("original_language"), match["title"])
            non_english += 1
            continue
        line = f"{seed['query']!r} -> {detail['title']!r} ({detail['year']}, {detail['media_type']})"
        if args.dry_run:
            print(f"  [dry-run] {line}")
            added += 1
            continue
        db.upsert_title(conn, detail_to_title_row(detail))
        db.add_rate_prompt(conn, detail["tmdb_id"], detail["media_type"])
        conn.commit()
        logger.info("added %s", line)
        added += 1

    logger.info("done: %d added, %d already-known, %d blocklisted, %d non-english, "
                "%d unmatched (tmdb calls: %d)", added, skipped_known, blocked_ct,
                non_english, unmatched, client.call_count)
    return 0


if __name__ == "__main__":
    sys.exit(main())
