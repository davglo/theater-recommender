"""Defensive parsing of Claude scoring + profile responses (malformed fixtures)."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from profile import parse_profile_json  # noqa: E402
from scorer import parse_scores  # noqa: E402

VALID = {(1, "tv"), (2, "movie")}


def entry(**overrides):
    e = {"tmdb_id": 1, "media_type": "tv", "cluster": config.CLUSTER_SCAMS,
         "fit_score": 72, "why": "matches investigative nonfiction signals", "dealbreakers": None}
    e.update(overrides)
    return e


class TestParseScores(unittest.TestCase):
    def test_clean_json(self):
        out = parse_scores(json.dumps([entry()]), VALID)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["fit_score"], 72)

    def test_strips_markdown_fences(self):
        text = "```json\n" + json.dumps([entry()]) + "\n```"
        self.assertEqual(len(parse_scores(text, VALID)), 1)

    def test_surrounding_prose_tolerated(self):
        text = "Here are the scores:\n" + json.dumps([entry()]) + "\nDone."
        self.assertEqual(len(parse_scores(text, VALID)), 1)

    def test_not_json_at_all(self):
        self.assertEqual(parse_scores("I cannot score these titles.", VALID), [])

    def test_unknown_tmdb_id_skipped(self):
        out = parse_scores(json.dumps([entry(tmdb_id=999)]), VALID)
        self.assertEqual(out, [])

    def test_id_media_type_pair_must_match_input(self):
        out = parse_scores(json.dumps([entry(tmdb_id=1, media_type="movie")]), VALID)
        self.assertEqual(out, [])

    def test_malformed_entries_skipped_good_ones_kept(self):
        text = json.dumps([
            entry(),
            entry(tmdb_id="not-an-int"),
            entry(tmdb_id=2, media_type="movie", fit_score="high"),
            "just a string",
            entry(tmdb_id=2, media_type="movie", why=""),
        ])
        out = parse_scores(text, VALID)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["tmdb_id"], 1)

    def test_fit_score_clamped(self):
        out = parse_scores(json.dumps([entry(fit_score=150),
                                       entry(tmdb_id=2, media_type="movie",
                                             fit_score=-5)]), VALID)
        self.assertEqual(out[0]["fit_score"], 100)
        self.assertEqual(out[1]["fit_score"], 0)

    def test_non_string_dealbreakers_coerced(self):
        out = parse_scores(json.dumps([entry(dealbreakers=["gore"])]), VALID)
        self.assertEqual(out[0]["dealbreakers"], "['gore']")

    def test_cluster_whitespace_snapped_to_canonical(self):
        out = parse_scores(json.dumps([entry(cluster="True Crime/Dark Nonfiction")]), VALID)
        self.assertEqual(out[0]["cluster"], config.CLUSTER_TRUE_CRIME)

    def test_unrecognized_cluster_kept_as_is(self):
        out = parse_scores(json.dumps([entry(cluster="Made Up Cluster")]), VALID)
        self.assertEqual(out[0]["cluster"], "Made Up Cluster")


class TestParseProfileJson(unittest.TestCase):
    def _profile(self, names=None):
        names = names or config.CLUSTER_NAMES
        return {"clusters": [{"name": n, "weight": 1.0, "anchors": [],
                              "signals": [], "notes": None} for n in names],
                "global_notes": "x"}

    def test_clean_profile(self):
        self.assertIsNotNone(parse_profile_json(json.dumps(self._profile())))

    def test_fenced_profile(self):
        text = "```json\n" + json.dumps(self._profile()) + "\n```"
        self.assertIsNotNone(parse_profile_json(text))

    def test_wrong_cluster_names_rejected(self):
        bad = self._profile(names=["Made Up Cluster"] + config.CLUSTER_NAMES[1:])
        self.assertIsNone(parse_profile_json(json.dumps(bad)))

    def test_garbage_rejected(self):
        self.assertIsNone(parse_profile_json("sorry, no"))
        self.assertIsNone(parse_profile_json('{"clusters": []}'))


if __name__ == "__main__":
    unittest.main()
