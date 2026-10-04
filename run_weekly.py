"""Weekly orchestrator: profile refresh → candidate pool → scoring → digest →
dashboard → run log. Scheduled Monday mornings via launchd.

    python3 run_weekly.py                          # full run
    python3 run_weekly.py --dry-run                # no writes, no Claude call
    python3 run_weekly.py --limit 10               # small pool for testing
    python3 run_weekly.py --force-profile-refresh  # re-derive profile now
"""
import argparse
import json
import logging
import logging.handlers
import sys
from typing import Optional

import config
import db
import digest as digest_mod
import profile as profile_mod
import render
import scorer
from candidate_pool import generate_pool
from tmdb_client import TMDBClient

logger = logging.getLogger("run_weekly")


class RunTagFilter(logging.Filter):
    """Injects the run tag into every record so all log lines are attributable."""

    def __init__(self, tag: str) -> None:
        super().__init__()
        self.tag = tag

    def filter(self, record: logging.LogRecord) -> bool:
        record.run_tag = self.tag
        return True


def setup_logging(tag: str) -> None:
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter(
        "%(asctime)s [%(run_tag)s] %(levelname)s %(name)s: %(message)s"
    )
    file_handler = logging.handlers.RotatingFileHandler(
        config.LOG_DIR / "run_weekly.log", maxBytes=512_000, backupCount=3
    )
    stream_handler = logging.StreamHandler(sys.stderr)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    run_filter = RunTagFilter(tag)
    for h in (file_handler, stream_handler):
        h.setFormatter(fmt)
        h.addFilter(run_filter)
        root.addHandler(h)


def main() -> int:
    parser = argparse.ArgumentParser(description="Weekly recommendation run")
    parser.add_argument("--dry-run", action="store_true",
                        help="no DB writes, no Claude call; print pool + prompt")
    parser.add_argument("--force-profile-refresh", action="store_true",
                        help="re-derive the taste profile regardless of decision count")
    parser.add_argument("--limit", type=int, default=None,
                        help="cap the candidate pool (testing)")
    args = parser.parse_args()

    conn = db.connect()
    run_tag = f"run{db.run_count(conn) + 1}" + ("-dry" if args.dry_run else "")
    setup_logging(run_tag)
    run_started_at = db.now_iso()
    logger.info("weekly run starting (dry_run=%s, limit=%s)", args.dry_run, args.limit)

    # 1. Profile: ensure v1 exists; re-derive if due.
    claude_calls = 0
    if args.dry_run:
        profile_row = db.latest_profile(conn) or {
            "version": 1, "profile_json": json.dumps(profile_mod.INITIAL_PROFILE)
        }
    else:
        profile_row = profile_mod.ensure_initial_profile(conn)
        due, n_decisions = profile_mod.rederive_due(conn)
        if not config.FIT_SCORING:
            logger.info("fit scoring off — skipping profile re-derivation")
        elif due or args.force_profile_refresh:
            logger.info("re-deriving profile (%d decisions since v%d)",
                        n_decisions, profile_row["version"])
            new_ver = profile_mod.rederive_profile(conn)
            if new_ver is not None:
                claude_calls += 1
                latest = db.latest_profile(conn)
                assert latest is not None
                profile_row = latest
        else:
            logger.info("profile v%s current (%d/%d decisions toward re-derivation)",
                        profile_row["version"], n_decisions, config.REDERIVE_INTERVAL)

    # 2. Candidate pool.
    tmdb = TMDBClient(config.get_tmdb_api_key())
    cap = args.limit or config.POOL_CAP
    pool, anchors = generate_pool(conn, tmdb, cap=cap, dry_run=args.dry_run)
    logger.info("pool: %d novel candidates (%d TMDB calls)", len(pool), tmdb.call_count)

    # 3. Score novel candidates (dry-run prints the prompt and stops writing).
    #    With fit scoring off there are no Claude calls at all.
    scored: list = []
    if config.FIT_SCORING:
        scored, score_calls = scorer.score_candidates(
            conn, pool, profile_row, dry_run=args.dry_run
        )
        claude_calls += score_calls

    if args.dry_run:
        logger.info("dry run complete: %d candidates, %d TMDB calls, no writes",
                    len(pool), tmdb.call_count)
        return 0

    # 4. Above-threshold titles become pending recommendations; the rest stay
    #    cached (never rendered, never re-fetched). Rejects are human-only.
    pending_before = conn.execute(
        "SELECT COUNT(*) AS n FROM title_status WHERE status='pending'"
    ).fetchone()["n"]
    above = 0
    if config.FIT_SCORING:
        for s in scored:
            if s["fit_score"] >= config.SCORE_THRESHOLD:
                db.set_status(conn, s["tmdb_id"], s["media_type"],
                              status="pending", source="weekly_run", decided=False)
                above += 1
        logger.info("scored %d candidates: %d above threshold (%d)",
                    len(scored), above, config.SCORE_THRESHOLD)
    else:
        # No scorer: every in-window candidate that survived the pool's
        # lane/English/blocklist/already-decided filters goes on the board.
        for c in pool:
            db.set_status(conn, c["tmdb_id"], c["media_type"],
                          status="pending", source="weekly_run", decided=False)
            above += 1
        logger.info("fit scoring off: %d recent titles added to the board", above)
    conn.commit()

    # 5. Digest + dashboard (dashboard sections are recency-based, not tied
    #    to this run — see digest.py).
    result = digest_mod.build_digest(conn, config.SCORE_THRESHOLD)
    render.render_dashboard(result)

    # 6. Run log. new_count/carryover_count describe THIS run's scoring
    #    activity, not the dashboard's recent/older split.
    run_id = db.insert_run(
        conn,
        ran_at=run_started_at,
        new_count=above,
        carryover_count=pending_before,
        api_calls_tmdb=tmdb.call_count,
        api_calls_claude=claude_calls,
        notes=f"anchors: {', '.join(anchors)}",
    )
    conn.commit()
    summary = result["summary"]
    logger.info(
        "run %d complete: %d added to the board, %d pending before "
        "this run | dashboard: %d new releases, %d older gems, %d watchlist | "
        "tmdb=%d claude=%d | dashboard: %s",
        run_id, above, pending_before, summary["recent_count"],
        summary["older_count"], summary["watchlist_count"],
        tmdb.call_count, claude_calls, config.DASHBOARD_PATH,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
