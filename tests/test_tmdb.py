"""Season-aware recency: which season counts as a show's latest release."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tmdb_client import TMDBClient, latest_aired_season  # noqa: E402

TODAY = "2026-10-04"


class TestLatestAiredSeason(unittest.TestCase):
    def test_picks_highest_aired_season(self):
        seasons = [{"season_number": 1, "air_date": "2024-03-07"},
                   {"season_number": 2, "air_date": "2026-09-20"}]
        self.assertEqual(latest_aired_season(seasons, TODAY)["season_number"], 2)

    def test_skips_specials_and_unaired(self):
        seasons = [{"season_number": 0, "air_date": "2026-09-30"},   # specials
                   {"season_number": 1, "air_date": "2024-03-07"},
                   {"season_number": 2, "air_date": "2027-01-01"},   # announced, not out
                   {"season_number": 3, "air_date": None}]
        self.assertEqual(latest_aired_season(seasons, TODAY)["season_number"], 1)

    def test_none_when_nothing_aired(self):
        self.assertIsNone(latest_aired_season([], TODAY))
        self.assertIsNone(latest_aired_season([{"season_number": 1, "air_date": "2027-02-01"}], TODAY))


class TestDiscoverParams(unittest.TestCase):
    def _capture(self, **kw):
        client = TMDBClient("k" * 32, sleep_seconds=0)
        seen = {}
        client._get = lambda path, params=None: seen.update(path=path, params=params) or {"results": []}
        client.discover(media_type=kw.pop("media_type", "tv"), with_genres="18", keyword_ids=[],
                        vote_floor=5, date_gte="2026-07-04", date_lte="2026-10-04", **kw)
        return seen

    def test_without_genres_passed_through(self):
        seen = self._capture(without_genres="10766,10764")
        self.assertEqual(seen["params"]["without_genres"], "10766,10764")

    def test_without_genres_omitted_by_default(self):
        self.assertNotIn("without_genres", self._capture()["params"])

    def test_tv_window_uses_episode_air_dates(self):
        params = self._capture()["params"]
        self.assertEqual((params["air_date.gte"], params["air_date.lte"]), ("2026-07-04", "2026-10-04"))
        movie = self._capture(media_type="movie")["params"]
        self.assertIn("primary_release_date.gte", movie)


if __name__ == "__main__":
    unittest.main()
