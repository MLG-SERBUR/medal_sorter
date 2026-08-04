from __future__ import annotations

import unittest

from medal_sorter.matching import KnownUser, resolve_observations


class MatchingTests(unittest.TestCase):
    def test_resolves_alias_and_keeps_evidence(self) -> None:
        users = [KnownUser(name="Alex Display", group="Raid team", aliases=("A1ex",))]
        observations = [
            {
                "text": "A1ex",
                "confidence": 0.96,
                "seen_at_s": 4.0,
                "bounding_box": [[0, 0], [1, 0], [1, 1], [0, 1]],
            }
        ]
        result = resolve_observations(observations, {"matching": {}}, users)

        self.assertEqual(result["state"], "complete")
        self.assertEqual(result["usernames"][0]["name"], "Alex Display")
        self.assertEqual(result["usernames"][0]["group"], "Raid team")
        self.assertEqual(result["usernames"][0]["occurrences"], 1)

    def test_short_names_require_exact_match(self) -> None:
        users = [KnownUser(name="Ace", group=None, aliases=())]
        observations = [{"text": "Axe", "confidence": 1.0, "seen_at_s": 1.0}]
        result = resolve_observations(observations, {"matching": {}}, users)

        self.assertEqual(result["usernames"], [])
