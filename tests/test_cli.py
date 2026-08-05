from __future__ import annotations

import unittest

from medal_sorter.cli import _needs_resolution, _record_ocr_success, _record_resolution
from medal_sorter.matching import KnownUser, resolution_settings_hash
from medal_sorter.ocr import OcrSettings


class CliTests(unittest.TestCase):
    def test_cached_resolution_skips_duplicate_recomputation(self) -> None:
        record = {
            "file": {"size_bytes": 123, "mtime_ns": 456},
            "analysis": {"usernames": {}},
        }
        settings = OcrSettings()
        users = [KnownUser(name="Alex Display", group=None, aliases=())]
        config = {"matching": {"minimum_ocr_confidence": 0.35, "fuzzy_threshold": 90}}
        resolution_hash = resolution_settings_hash(config, users)

        ocr_result = {
            "duration_seconds": 5.0,
            "requested_sample_times_s": [0.5],
            "decoded_sample_count": 1,
            "observations": [
                {"seen_at_s": 0.5, "text": "Alex Display", "confidence": 0.96, "bounding_box": [[0, 0], [1, 0], [1, 1], [0, 1]]}
            ],
            "engine": {"engine": "dummy"},
        }

        _record_ocr_success(record, ocr_result, settings)
        self.assertTrue(_needs_resolution(record, resolution_hash))

        _record_resolution(record, config, users)
        self.assertFalse(_needs_resolution(record, resolution_hash))
        self.assertEqual(record["analysis"]["usernames"]["resolution"]["ocr_completed_at"], record["analysis"]["usernames"]["ocr"]["completed_at"])
        self.assertEqual(record["analysis"]["usernames"]["resolution"]["state"], "complete")
