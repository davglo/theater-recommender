"""Season-aware recency: which season counts as a show's latest release."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tmdb_client import latest_aired_season  # noqa: E402

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


if __name__ == "__main__":
    unittest.main()
