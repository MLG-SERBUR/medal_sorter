from __future__ import annotations

import unittest
from pathlib import Path

from medal_sorter.matching import load_friends_config, KnownUser


class MatchingYamlTests(unittest.TestCase):
    def test_loads_group_sections_from_yaml(self) -> None:
        path = Path("test_friends.yaml")
        path.write_text(
            """
schema_version: 1
ignored_ocr_text:
  - Play
matching:

  fuzzy_threshold: 90

ar:
  - crymeow
  - 0C_c0
  - 0Cc0

memeteam:
  - MMM10
  - T0ny Stank
  - Tony Stank
""",
            encoding="utf-8",
        )
        try:
            config, users = load_friends_config(path)
        finally:
            path.unlink()

        self.assertEqual(config["schema_version"], 1)
        self.assertEqual(config["ignored_ocr_text"], ["Play"])
        self.assertEqual(config["matching"]["fuzzy_threshold"], 90)
        self.assertEqual(len(users), 5)
        self.assertTrue(any(user.name == "0C_c0" and user.group == "ar" for user in users))
        self.assertFalse(any(user.name == "0Cc0" for user in users))
        self.assertTrue(any(user.name == "T0ny Stank" and user.group == "memeteam" for user in users))

    def test_loads_alias_mapping_in_yaml(self) -> None:
        path = Path("test_friends_alias.yaml")
        path.write_text(
            """
schema_version: 1

mygroup:
  - "Alex Discord": ["A1ex", "AlexOnSteam"]
""",
            encoding="utf-8",
        )
        try:
            _, users = load_friends_config(path)
        finally:
            path.unlink()

        self.assertEqual(len(users), 3)
        self.assertTrue(any(user.name == "Alex Discord" for user in users))
        self.assertTrue(any(user.name == "A1ex" for user in users))
        self.assertTrue(any(user.name == "AlexOnSteam" for user in users))
