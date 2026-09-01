from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from medal_sorter.matching import KnownUser, has_any_match
from medal_sorter.ocr import OcrEngine, OcrSettings


class DummyCapture:
    def __init__(self, frames: float, fps: float) -> None:
        self._frames = frames
        self._fps = fps
        self._opened = True

    def isOpened(self) -> bool:
        return self._opened

    def get(self, prop: Any) -> float:
        if prop == self._cv2.CAP_PROP_FRAME_COUNT:
            return self._frames
        if prop == self._cv2.CAP_PROP_FPS:
            return self._fps
        return 0.0

    def set(self, prop: Any, value: float) -> None:
        pass

    def read(self) -> tuple[bool, str]:
        return True, self._current_frame

    def release(self) -> None:
        self._opened = False


class DummyReader:
    def __init__(self, observations_by_timestamp: dict[float, list[tuple[Any, str, float]]]) -> None:
        self._observations_by_timestamp = observations_by_timestamp

    def readtext(self, frame: Any, **kwargs: Any) -> list[tuple[Any, str, float]]:
        return self._observations_by_timestamp.get(frame, [])


class OcrTests(unittest.TestCase):
    def test_progressive_sampling_stops_when_match_found(self) -> None:
        duration_seconds = 5.0
        capture = DummyCapture(frames=25.0, fps=5.0)

        dummy_cv2 = SimpleNamespace(
            VideoCapture=lambda path: capture,
            CAP_PROP_FRAME_COUNT=7,
            CAP_PROP_FPS=8,
            CAP_PROP_POS_MSEC=9,
            IMREAD_COLOR=1,
        )
        capture._cv2 = dummy_cv2

        observations_by_timestamp = {
            0.5: [([[0, 0], [1, 0], [1, 1], [0, 1]], "nope", 0.9)],
            2.5: [([[0, 0], [1, 0], [1, 1], [0, 1]], "Alex Display", 0.9)],
        }
        dummy_reader = DummyReader(observations_by_timestamp)

        class DummyOcrEngine(OcrEngine):
            def _initialize(self, languages: tuple[str, ...]) -> None:
                self._cv2 = dummy_cv2
                self._reader = dummy_reader
                self._metadata = {"engine": "dummy", "device": "CPU"}

            def _read_frame(self, capture: Any, path: Path, timestamp: float) -> Any | None:
                return timestamp

        engine = DummyOcrEngine(Path("dummy_video.mp4"))

        settings = OcrSettings(sample_interval_seconds=2.0, maximum_samples=3, minimum_saved_confidence=0.25)
        users = [KnownUser(name="Alex Display", group=None, aliases=())]
        config = {"matching": {"fuzzy_threshold": 90}}

        def match_found(frame_observations: list[dict[str, Any]]) -> bool:
            return has_any_match(frame_observations, config, users)

        result = engine.analyze_video(Path("dummy_video.mp4"), settings, match_found=match_found)

        self.assertEqual(result["requested_sample_times_s"], [0.5, 2.5])
        self.assertEqual(result["decoded_sample_count"], 2)
        self.assertEqual(len(result["observations"]), 2)
        self.assertTrue(any(obs["text"] == "Alex Display" for obs in result["observations"]))

    def test_progressive_sampling_refines_after_coarse_misses(self) -> None:
        capture = DummyCapture(frames=25.0, fps=5.0)

        dummy_cv2 = SimpleNamespace(
            VideoCapture=lambda path: capture,
            CAP_PROP_FRAME_COUNT=7,
            CAP_PROP_FPS=8,
            CAP_PROP_POS_MSEC=9,
            IMREAD_COLOR=1,
        )
        capture._cv2 = dummy_cv2

        observations_by_timestamp = {
            0.5: [([[0, 0], [1, 0], [1, 1], [0, 1]], "nope", 0.9)],
            1.5: [([[0, 0], [1, 0], [1, 1], [0, 1]], "Alex Display", 0.9)],
            4.5: [([[0, 0], [1, 0], [1, 1], [0, 1]], "nope", 0.9)],
        }
        dummy_reader = DummyReader(observations_by_timestamp)

        class DummyOcrEngine(OcrEngine):
            def _initialize(self, languages: tuple[str, ...]) -> None:
                self._cv2 = dummy_cv2
                self._reader = dummy_reader
                self._metadata = {"engine": "dummy", "device": "CPU"}

            def _read_frame(self, capture: Any, path: Path, timestamp: float) -> Any | None:
                return timestamp

        engine = DummyOcrEngine(Path("dummy_video.mp4"))

        settings = OcrSettings(sample_interval_seconds=4.0, maximum_samples=8, minimum_saved_confidence=0.25)
        users = [KnownUser(name="Alex Display", group=None, aliases=())]
        config = {"matching": {"fuzzy_threshold": 90}}

        def match_found(frame_observations: list[dict[str, Any]]) -> bool:
            return has_any_match(frame_observations, config, users)

        result = engine.analyze_video(Path("dummy_video.mp4"), settings, match_found=match_found)

        self.assertEqual(result["requested_sample_times_s"], [0.5, 4.5, 2.5, 1.5])
        self.assertEqual(result["decoded_sample_count"], 4)
        self.assertEqual(len(result["observations"]), 3)
        self.assertEqual(result["observations"][0]["seen_at_s"], 0.5)
        self.assertEqual(result["observations"][1]["seen_at_s"], 4.5)
        self.assertEqual(result["observations"][2]["seen_at_s"], 1.5)
        self.assertTrue(any(obs["text"] == "Alex Display" for obs in result["observations"]))
