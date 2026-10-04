"""Score every fetched-but-unscored candidate under the current profile, then
promote above-threshold ones to pending and rebuild the dashboard.

Use to recover after a run whose scoring was interrupted (e.g. the Claude Code
CLI was logged out): the candidates are already in `titles`, they just need
scoring. Safe to re-run — cached scores are never re-scored.

    python3 resume_scoring.py
"""
import json
import logging
import sys

import config
import db
import digest as digest_mod
import render
import scorer

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("resume_scoring")


def main() -> int:
    conn = db.connect()
    profile_row = db.latest_profile(conn)
    logger.info("using profile v%s", profile_row["version"])

    scored = db.scored_keys(conn)
    candidates = []
    for r in conn.execute("SELECT * FROM titles").fetchall():
        key = (r["tmdb_id"], r["media_type"])
        if key in scored:
            continue
        # Skip titles the user has already decided on (seen/rejected/etc.).
        st = conn.execute(
            "SELECT 1 FROM title_status WHERE tmdb_id=? AND media_type=?", key
        ).fetchone()
        if st:
            continue
        candidates.append({
            "tmdb_id": r["tmdb_id"], "media_type": r["media_type"], "title": r["title"],
            "year": r["year"], "genre_names": json.loads(r["genres"]) if r["genres"] else [],
            "keyword_names": json.loads(r["keywords"]) if r["keywords"] else [],
            "overview": r["overview"], "tmdb_rating": r["tmdb_rating"],
        })
    logger.info("%d unscored candidates to score", len(candidates))
    if not candidates:
        logger.info("nothing to score")
        return 0

    scored_rows, calls = scorer.score_candidates(conn, candidates, profile_row)
    logger.info("scored %d/%d (%d CLI calls)", len(scored_rows), len(candidates), calls)
    if calls == 0 and scored_rows == []:
        logger.error("no candidates scored — is the Claude Code CLI logged in? "
                     "(run: claude  then /login)")
        return 1

    above = 0
    for s in scored_rows:
        if s["fit_score"] >= config.SCORE_THRESHOLD:
            db.set_status(conn, s["tmdb_id"], s["media_type"], status="pending",
                          source="weekly_run", decided=False)
            above += 1
    conn.commit()
    logger.info("%d above threshold (%d)", above, config.SCORE_THRESHOLD)

    result = digest_mod.build_digest(conn, config.SCORE_THRESHOLD)
    render.render_dashboard(result)
    s = result["summary"]
    logger.info("dashboard: %d new releases, %d older gems, %d watchlist",
                s["recent_count"], s["older_count"], s["watchlist_count"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
