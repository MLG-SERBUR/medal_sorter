from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from triage.triage import Triage


class TriageTests(unittest.TestCase):
    def test_skip_marked_can_be_disabled_to_revisit_clips(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            medal_root = root / "medal"
            clip_dir = medal_root / "Clips" / "Game"
            clip_dir.mkdir(parents=True)
            db_path = root / "clip_data.sqlite"
            records = []
            for name in ("keep.mp4", "delete.mp4", "unmarked.mp4"):
                (clip_dir / name).touch()
                data = {
                    "path": f"Clips/Game/{name}",
                    "kind": "source",
                    "analysis": {
                        "usernames": {
                            "resolution": {"usernames": [{"name": "Friend", "group": "team"}]}
                        }
                    },
                    "file": {"mtime_ns": len(records) + 1, "size_bytes": 1},
                }
                records.append((f"clips/game/{name}", json.dumps(data)))

            conn = sqlite3.connect(db_path)
            try:
                conn.execute("CREATE TABLE records (clip_path TEXT, data TEXT)")
                conn.executemany("INSERT INTO records VALUES (?, ?)", records)
                conn.commit()
            finally:
                conn.close()

            triage = Triage(db_path, medal_root)
            try:
                triage.set_keep("clips/game/keep.mp4", True)
                triage.set_discard("clips/game/delete.mp4", True)

                visible = triage.clips("Game", "team", show_sources=False)
                self.assertEqual([clip["key"] for clip in visible], ["clips/game/unmarked.mp4"])

                all_clips = triage.clips(
                    "Game", "team", show_sources=False, skip_marked=False
                )
                self.assertEqual(
                    [clip["key"] for clip in all_clips],
                    ["clips/game/keep.mp4", "clips/game/delete.mp4", "clips/game/unmarked.mp4"],
                )
            finally:
                triage.conn.close()


if __name__ == "__main__":
    unittest.main()
