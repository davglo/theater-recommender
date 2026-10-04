"""One-time backfill: populate release_date/trailer_url for titles inserted
before those columns existed. Safe to re-run — only touches rows where
release_date IS NULL (the migration marker), and refetching an already-backfilled
row is harmless if ever needed via --force.

Usage:
    python3 backfill_metadata.py --dry-run     # preview, no writes, no TMDB calls skipped
    python3 backfill_metadata.py               # backfill for real
    python3 backfill_metadata.py --limit 10    # test on a small batch
"""
import argparse
import logging
import sys

import config
import db
from tmdb_client import TMDBClient, detail_to_title_row

logger = logging.getLogger("backfill_metadata")


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill release_date/trailer_url")
    parser.add_argument("--dry-run", action="store_true", help="list rows, make no calls or writes")
    parser.add_argument("--force", action="store_true",
                        help="also refetch rows that already have a release_date")
    parser.add_argument("--limit", type=int, default=None, help="cap rows processed (testing)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    conn = db.connect()
    where = "" if args.force else "WHERE release_date IS NULL"
    sql = f"SELECT tmdb_id, media_type, title FROM titles {where} ORDER BY tmdb_id"
    if args.limit:
        sql += f" LIMIT {args.limit}"
    rows = conn.execute(sql).fetchall()
    logger.info("%d titles to backfill", len(rows))

    if args.dry_run:
        for r in rows:
            print(f"  [dry-run] {r['title']} ({r['media_type']}, id={r['tmdb_id']})")
        return 0

    client = TMDBClient(config.get_tmdb_api_key())
    updated, failed = 0, 0
    for r in rows:
        try:
            detail = client.details(r["media_type"], r["tmdb_id"])
        except Exception as exc:  # noqa: BLE001 — log and continue past any single bad title
            logger.warning("failed to fetch %s/%s: %s", r["tmdb_id"], r["media_type"], exc)
            failed += 1
            continue
        db.upsert_title(conn, detail_to_title_row(detail))
        updated += 1
        if updated % 25 == 0:
            conn.commit()
            logger.info("progress: %d/%d", updated, len(rows))
    conn.commit()
    logger.info("done: %d updated, %d failed (tmdb calls: %d)",
                updated, failed, client.call_count)
    return 0


if __name__ == "__main__":
    sys.exit(main())
