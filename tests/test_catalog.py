from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from medal_sorter.catalog import synchronize_catalog
from medal_sorter.store import new_database


class CatalogTests(unittest.TestCase):
    def test_links_known_edit_suffixes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            clips = root / "Clips" / "Game"
            edits = root / "Edits"
            clips.mkdir(parents=True)
            edits.mkdir()
            (clips / "Clip123.mp4").touch()
            for suffix in (
                "--edit",
                "--tr-edit",
                "-tr-edit",
                "-trim-1783681927857",
                "--edit-7fb6",
            ):
                (edits / f"Clip123{suffix}.mp4").touch()

            database = new_database(root)
            summary = synchronize_catalog(database, root)

            self.assertEqual(summary.source_count, 1)
            self.assertEqual(summary.audio_track_count, 0)
            self.assertEqual(summary.linked_edit_count, 5)
            source = database["records"]["clips/game/clip123.mp4"]
            self.assertEqual(len(source["links"]["edits"]), 5)

    def test_does_not_guess_ambiguous_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for game in ("Game A", "Game B"):
                directory = root / "Clips" / game
                directory.mkdir(parents=True, exist_ok=True)
                (directory / "SameName.mp4").touch()
            edits = root / "Edits"
            edits.mkdir()
            (edits / "SameName--edit.mp4").touch()

            database = new_database(root)
            summary = synchronize_catalog(database, root)

            self.assertEqual(summary.linked_edit_count, 0)
            self.assertEqual(summary.unresolved_edit_count, 1)
            edit = database["records"]["edits/samename--edit.mp4"]
            self.assertEqual(edit["source"]["state"], "unresolved")
            self.assertEqual(len(edit["source"]["candidates"]), 2)

    def test_audio_tracks_linked_to_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            clips = root / "Clips" / "Marvel Rivals"
            clips.mkdir(parents=True)
            (clips / "ClipABC.mp4").touch()
            (clips / "ClipABC_discord.mp4").touch()
            (clips / "ClipABC_game.mp4").touch()
            (clips / "ClipABC_mic.mp4").touch()

            database = new_database(root)
            summary = synchronize_catalog(database, root)

            self.assertEqual(summary.source_count, 1)
            self.assertEqual(summary.audio_track_count, 3)
            source = database["records"]["clips/marvel rivals/clipabc.mp4"]
            self.assertEqual(len(source["links"]["audio_tracks"]), 3)
            discord_track = database["records"]["clips/marvel rivals/clipabc_discord.mp4"]
            self.assertEqual(discord_track["kind"], "audio_track")
            self.assertEqual(discord_track["track_kind"], "discord")
            self.assertEqual(discord_track["source"]["state"], "linked")
