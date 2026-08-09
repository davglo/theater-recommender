"""Dashboard section classification: recent (by release date) vs.
recommendations vs. watchlist vs. hidden."""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import db  # noqa: E402
from digest import build_digest, classify, is_recent_release  # noqa: E402


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
        self.assertEqual(classify("pending", 80, 40, OLD_DATE), "recommendations")

    def test_pending_unknown_release_is_recommendation(self):
        self.assertEqual(classify("pending", 80, 40, None), "recommendations")

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
        self.assertEqual([x["tmdb_id"] for x in d["recommendations"]], [3, 7, 2])  # fit desc
        self.assertEqual([x["tmdb_id"] for x in d["watchlist"]], [5])
        self.assertEqual(d["summary"]["recent_count"], 1)
        self.assertEqual(d["summary"]["recommendations_count"], 3)
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


if __name__ == "__main__":
    unittest.main()
