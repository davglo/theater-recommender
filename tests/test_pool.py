"""Dedupe + cap logic for the weekly candidate pool."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from candidate_pool import (apply_cap, credits_to_candidates,  # noqa: E402
                            dedupe_pool, drop_blocklisted, drop_known, in_window,
                            prefilter_recent, select_pool)
from db import text_matches_blocklist  # noqa: E402


def cand(tmdb_id, media_type="tv", bucket="A", popularity=1.0, title="", overview=""):
    return {"tmdb_id": tmdb_id, "media_type": media_type,
            "source_bucket": bucket, "popularity": popularity,
            "title": title, "overview": overview}


class TestBlocklist(unittest.TestCase):
    def test_matches_case_insensitive(self):
        self.assertEqual(text_matches_blocklist("The Greatest: Muhammad ALI Story",
                                                ["muhammad ali"]), "muhammad ali")

    def test_no_match(self):
        self.assertIsNone(text_matches_blocklist("Senna", ["muhammad ali"]))

    def test_empty_terms(self):
        self.assertIsNone(text_matches_blocklist("anything", []))

    def test_drop_blocklisted_matches_title_only(self):
        # Title names the subject -> dropped. Overview-only mention -> kept
        # (respects "only if it's ABOUT them"; the scorer judges those).
        pool = [
            cand(1, title="Muhammad Ali: The Greatest", overview="boxing"),
            cand(2, title="Senna", overview="F1 driver"),
            cand(3, title="NFL Dynasties", overview="features the Dallas Cowboys"),
        ]
        out = drop_blocklisted(pool, ["muhammad ali", "dallas cowboys"])
        self.assertEqual([c["tmdb_id"] for c in out], [2, 3])

    def test_drop_blocklisted_no_terms_is_noop(self):
        pool = [cand(1, title="Ali")]
        self.assertEqual(len(drop_blocklisted(pool, [])), 1)


class TestDedupe(unittest.TestCase):
    def test_drops_duplicate_keys_keeps_first(self):
        pool = [cand(1, bucket="A"), cand(1, bucket="B"), cand(2)]
        out = dedupe_pool(pool)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["source_bucket"], "A")

    def test_same_id_different_media_type_is_not_duplicate(self):
        pool = [cand(1, "tv"), cand(1, "movie")]
        self.assertEqual(len(dedupe_pool(pool)), 2)

    def test_drops_null_ids(self):
        pool = [cand(None), cand(2)]
        out = dedupe_pool(pool)
        self.assertEqual([c["tmdb_id"] for c in out], [2])

    def test_drop_known_removes_any_status_or_score(self):
        pool = [cand(1), cand(2), cand(3)]
        out = drop_known(pool, {(1, "tv"), (3, "tv")})
        self.assertEqual([c["tmdb_id"] for c in out], [2])


class TestApplyCap(unittest.TestCase):
    def test_under_cap_untouched(self):
        pool = [cand(i) for i in range(5)]
        self.assertEqual(len(apply_cap(pool, 10)), 5)

    def test_cap_respected(self):
        pool = [cand(i) for i in range(50)]
        self.assertEqual(len(apply_cap(pool, 30)), 30)

    def test_no_bucket_starves(self):
        # 20 from bucket A, 4 from bucket B, cap 8 -> B keeps all 4.
        pool = [cand(i, bucket="A", popularity=100 - i) for i in range(20)]
        pool += [cand(100 + i, bucket="B", popularity=1) for i in range(4)]
        out = apply_cap(pool, 8)
        b_count = sum(1 for c in out if c["source_bucket"] == "B")
        self.assertEqual(len(out), 8)
        self.assertEqual(b_count, 4)

    def test_popularity_order_within_bucket(self):
        pool = [cand(1, popularity=5), cand(2, popularity=50), cand(3, popularity=10),
                cand(4, popularity=1)]
        out = apply_cap(pool, 2)
        self.assertEqual([c["tmdb_id"] for c in out], [2, 3])


class TestSelectPool(unittest.TestCase):
    def _mix(self, n_tv, n_movie):
        pool = [cand(i, media_type="tv", popularity=n_tv - i) for i in range(n_tv)]
        pool += [cand(1000 + i, media_type="movie", popularity=n_movie - i)
                 for i in range(n_movie)]
        return pool

    def test_plentiful_both_holds_movie_fraction(self):
        out = select_pool(self._mix(300, 100), cap=200, movie_max_fraction=0.25)
        movies = sum(1 for c in out if c["media_type"] == "movie")
        self.assertEqual(len(out), 200)
        self.assertEqual(movies, 50)  # exactly 25%

    def test_series_short_shrinks_pool_not_pad_with_movies(self):
        # Only 94 novel series available; movies must stay ~25% of the real pool.
        out = select_pool(self._mix(94, 200), cap=200, movie_max_fraction=0.25)
        tv = sum(1 for c in out if c["media_type"] == "tv")
        movies = sum(1 for c in out if c["media_type"] == "movie")
        self.assertEqual(tv, 94)
        self.assertLessEqual(movies, int(94 * 0.25 / 0.75) + 1)  # ~31, never 100+
        self.assertLess(movies, tv)

    def test_movies_short_gives_slots_to_series(self):
        out = select_pool(self._mix(300, 10), cap=200, movie_max_fraction=0.25)
        self.assertEqual(len(out), 200)
        self.assertEqual(sum(1 for c in out if c["media_type"] == "movie"), 10)


class TestCreditsToCandidates(unittest.TestCase):
    def test_crew_only_deduped_and_tagged(self):
        credits = {
            "cast": [{"id": 9, "media_type": "movie", "title": "Cameo"}],
            "crew": [
                {"id": 1, "media_type": "movie", "title": "Snatch", "job": "Director"},
                {"id": 1, "media_type": "movie", "title": "Snatch", "job": "Writer"},
                {"id": 2, "media_type": "tv", "name": "MobLand", "job": "Director"},
                {"id": 3, "media_type": "person", "name": "junk"},
                {"id": None, "media_type": "movie", "title": "No id"},
            ],
        }
        out = credits_to_candidates(credits, "Prestige")
        self.assertEqual([(c["tmdb_id"], c["media_type"]) for c in out],
                         [(1, "movie"), (2, "tv")])  # cast cameo, dupes, junk dropped
        self.assertEqual(out[1]["title"], "MobLand")
        self.assertTrue(all(c["source_bucket"] == "Prestige" for c in out))

    def test_empty_payload(self):
        self.assertEqual(credits_to_candidates({}, "Prestige"), [])


class TestRecentWindow(unittest.TestCase):
    START, END = "2026-07-01", "2026-10-01"

    def test_in_window_edges(self):
        self.assertTrue(in_window("2026-07-01", self.START, self.END))
        self.assertTrue(in_window("2026-10-01", self.START, self.END))
        self.assertFalse(in_window("2026-06-30", self.START, self.END))
        self.assertFalse(in_window("2026-10-02", self.START, self.END))  # future = not out
        self.assertFalse(in_window(None, self.START, self.END))

    def test_prefilter_keeps_tv_drops_old_movies(self):
        pool = [
            {"tmdb_id": 1, "media_type": "movie", "release_date": "2026-08-15"},
            {"tmdb_id": 2, "media_type": "movie", "release_date": "2001-01-01"},
            {"tmdb_id": 3, "media_type": "movie", "release_date": None},
            # Old premiere, but may have a new season — decided after details().
            {"tmdb_id": 4, "media_type": "tv", "release_date": "2013-09-12"},
        ]
        out = prefilter_recent(pool, self.START, self.END)
        self.assertEqual([c["tmdb_id"] for c in out], [1, 4])


if __name__ == "__main__":
    unittest.main()
