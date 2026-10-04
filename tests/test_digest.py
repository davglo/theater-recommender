"""Dashboard sections: recent (season-aware release date) vs. older gems vs.
watchlist vs. hidden, plus lane-relative buzz ranking."""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import db  # noqa: E402
from digest import apply_buzz, build_digest, classify, is_recent_release  # noqa: E402


def days_ago(n):
    return (datetime.now(timezone.utc).date() - timedelta(days=n)).isoformat()


RECENT_DATE = days_ago(5)
OLD_DATE = days_ago(400)
BOUNDARY_DATE = days_ago(config.RECENT_RELEASE_DAYS)
JUST_PAST_BOUNDARY = days_ago(config.RECENT_RELEASE_DAYS + 1)


class TestIsRecentRelease(unittest.TestCase):
    def test_within_window(self):
        self.assertTrue(is_recent_release(RECENT_DATE))

    def test_at_boundary_is_recent(self):
        self.assertTrue(is_recent_release(BOUNDARY_DATE))

    def test_just_past_boundary_is_not_recent(self):
        self.assertFalse(is_recent_release(JUST_PAST_BOUNDARY))

    def test_far_in_past_is_not_recent(self):
        self.assertFalse(is_recent_release(OLD_DATE))

    def test_unknown_date_is_not_recent(self):
        self.assertFalse(is_recent_release(None))
        self.assertFalse(is_recent_release(""))

    def test_future_date_is_not_recent(self):
        future = (datetime.now(timezone.utc).date() + timedelta(days=5)).isoformat()
        self.assertFalse(is_recent_release(future))

    def test_malformed_date_is_not_recent(self):
        self.assertFalse(is_recent_release("not-a-date"))


class TestClassify(unittest.TestCase):
    def test_pending_recent_release(self):
        self.assertEqual(classify("pending", 80, 40, RECENT_DATE), "recent")

    def test_pending_old_release_is_recommendation(self):
        self.assertEqual(classify("pending", 80, 40, OLD_DATE), "older")

    def test_pending_unknown_release_is_recommendation(self):
        self.assertEqual(classify("pending", 80, 40, None), "older")

    def test_watchlist_pinned_regardless_of_score_or_date(self):
        self.assertEqual(classify("watchlist", 10, 40, OLD_DATE), "watchlist")
        self.assertEqual(classify("watchlist", None, 40, None), "watchlist")

    def test_sub_threshold_hidden(self):
        self.assertIsNone(classify("pending", 39, 40, RECENT_DATE))

    def test_threshold_boundary_shown(self):
        self.assertEqual(classify("pending", 40, 40, RECENT_DATE), "recent")

    def test_seen_and_rejected_never_render(self):
        self.assertIsNone(classify("seen", 95, 40, RECENT_DATE))
        self.assertIsNone(classify("not_interested", 95, 40, RECENT_DATE))

    def test_unscored_pending_hidden(self):
        self.assertIsNone(classify("pending", None, 40, RECENT_DATE))


class TestBuildDigest(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(Path(":memory:"))

    def _title(self, tmdb_id, status, fit=None, release_date=None):
        db.upsert_title(self.conn, {
            "tmdb_id": tmdb_id, "media_type": "tv", "title": f"T{tmdb_id}",
            "year": 2025, "release_date": release_date,
        })
        db.set_status(self.conn, tmdb_id, "tv", status, "weekly_run", decided=False)
        if fit is not None:
            db.insert_score(self.conn, {
                "tmdb_id": tmdb_id, "media_type": "tv", "cluster": "Scams, Scandal & Corruption",
                "fit_score": fit, "why": "w", "dealbreakers": None,
                "scored_at": "2026-01-01T00:00:00+00:00", "profile_ver": 1,
            })

    def test_sections_and_sorting(self):
        self._title(1, "pending", fit=90, release_date=RECENT_DATE)       # recent
        self._title(2, "pending", fit=55, release_date=OLD_DATE)          # recommendation
        self._title(3, "pending", fit=99, release_date=OLD_DATE)          # recommendation, higher fit
        self._title(4, "pending", fit=20, release_date=RECENT_DATE)       # hidden (sub-threshold)
        self._title(5, "watchlist", fit=70, release_date=OLD_DATE)        # pinned
        self._title(6, "seen", fit=95, release_date=RECENT_DATE)          # never rendered
        self._title(7, "pending", fit=60, release_date=None)              # recommendation, unknown date

        d = build_digest(self.conn, threshold=40)
        self.assertEqual([x["tmdb_id"] for x in d["recent"]], [1])
        self.assertEqual([x["tmdb_id"] for x in d["older"]], [3, 7, 2])  # fit desc
        self.assertEqual([x["tmdb_id"] for x in d["watchlist"]], [5])
        self.assertEqual(d["summary"]["recent_count"], 1)
        self.assertEqual(d["summary"]["older_count"], 3)
        self.assertEqual(d["summary"]["watchlist_count"], 1)

    def _bare_title(self, tmdb_id, rating=None):
        db.upsert_title(self.conn, {
            "tmdb_id": tmdb_id, "media_type": "tv", "title": f"H{tmdb_id}",
            "year": 2020, "tmdb_rating": rating,
        })

    def test_rate_history_shows_only_undecided_prompts(self):
        self._bare_title(10, rating=8.5)
        self._bare_title(11, rating=9.0)
        self._bare_title(12, rating=7.0)
        for t in (10, 11, 12):
            db.add_rate_prompt(self.conn, t, "tv")
        # 12 already rated -> should NOT appear in the wall
        db.set_status(self.conn, 12, "tv", "seen", "manual", decided=True, rating=4)

        d = build_digest(self.conn, threshold=40)
        ids = [x["tmdb_id"] for x in d["rate_history"]]
        self.assertEqual(ids, [11, 10])  # tmdb_rating desc, decided one excluded
        self.assertEqual(d["summary"]["rate_history_count"], 2)

    def test_clear_rate_prompt_removes_from_wall(self):
        self._bare_title(20, rating=8.0)
        db.add_rate_prompt(self.conn, 20, "tv")
        self.assertEqual(len(build_digest(self.conn, 40)["rate_history"]), 1)
        db.clear_rate_prompt(self.conn, 20, "tv")
        self.assertEqual(len(build_digest(self.conn, 40)["rate_history"]), 0)


def _item(tid, cluster, fit, pop):
    return {"tmdb_id": tid, "cluster": cluster, "fit_score": fit, "popularity": pop}


class TestApplyBuzz(unittest.TestCase):
    def test_buzz_is_relative_within_lane(self):
        # Docs have tiny TMDB popularity vs scripted; the hottest of each lane
        # must get the same max buzz.
        items = [_item(1, "Docs", 50, 1.0), _item(2, "Docs", 50, 5.0),
                 _item(3, "Drama", 50, 100.0), _item(4, "Drama", 50, 900.0)]
        apply_buzz(items)
        b = {it["tmdb_id"]: it["buzz"] for it in items}
        self.assertEqual(b[2], config.BUZZ_MAX)
        self.assertEqual(b[4], config.BUZZ_MAX)
        self.assertEqual(b[1], 0)
        self.assertTrue(items[1]["hot"])
        self.assertFalse(items[0]["hot"])

    def test_buzz_breaks_ties_but_strong_fit_wins(self):
        items = [_item(1, "L", 90, 1.0),    # strong fit, cold
                 _item(2, "L", 70, 999.0),  # weaker fit, hottest
                 _item(3, "L", 75, 2.0)]
        apply_buzz(items)
        order = [it["tmdb_id"] for it in sorted(items, key=lambda x: -x["rank_score"])]
        self.assertEqual(order[0], 1)       # 90 + 0 beats 70 + 12
        self.assertEqual(order, [1, 2, 3])  # 82 (hot) beats 75 + 6

    def test_single_item_lane_gets_neutral_buzz(self):
        items = [_item(1, "Solo", 60, 10.0)]
        apply_buzz(items)
        self.assertEqual(items[0]["buzz"], round(config.BUZZ_MAX * 0.5))
        self.assertEqual(items[0]["rank_score"], 60 + items[0]["buzz"])

    def test_missing_popularity_treated_as_zero(self):
        items = [_item(1, "L", 60, None), _item(2, "L", 60, 3.0)]
        apply_buzz(items)
        self.assertEqual(items[0]["buzz"], 0)


class TestOlderGemsCap(unittest.TestCase):
    def test_older_capped_to_top_fits(self):
        conn = db.connect(Path(":memory:"))
        n = config.OLDER_GEMS_CAP + 3
        for i in range(n):
            db.upsert_title(conn, {"tmdb_id": i, "media_type": "tv", "title": f"T{i}",
                                   "year": 2020, "release_date": OLD_DATE})
            db.set_status(conn, i, "tv", "pending", "weekly_run", decided=False)
            db.insert_score(conn, {"tmdb_id": i, "media_type": "tv", "cluster": "C",
                                   "fit_score": 50 + i, "why": "w", "dealbreakers": None,
                                   "scored_at": "2026-01-01T00:00:00+00:00", "profile_ver": 1})
        d = build_digest(conn, threshold=40)
        self.assertEqual(len(d["older"]), config.OLDER_GEMS_CAP)
        self.assertEqual(d["older"][0]["tmdb_id"], n - 1)      # highest fit first
        self.assertEqual(d["summary"]["older_total"], n)

    def test_new_season_of_old_show_counts_as_recent(self):
        conn = db.connect(Path(":memory:"))
        # Premiered years ago, but latest season premiered 5 days ago.
        db.upsert_title(conn, {"tmdb_id": 1, "media_type": "tv", "title": "Returning",
                               "year": 2019, "release_date": "2019-01-01",
                               "recent_date": RECENT_DATE, "latest_season": 3})
        db.set_status(conn, 1, "tv", "pending", "weekly_run", decided=False)
        db.insert_score(conn, {"tmdb_id": 1, "media_type": "tv", "cluster": "C",
                               "fit_score": 80, "why": "w", "dealbreakers": None,
                               "scored_at": "2026-01-01T00:00:00+00:00", "profile_ver": 1})
        d = build_digest(conn, threshold=40)
        self.assertEqual([x["tmdb_id"] for x in d["recent"]], [1])
        self.assertTrue(d["recent"][0]["recency_label"].startswith("New season 3"))


if __name__ == "__main__":
    unittest.main()
