from __future__ import annotations

import math
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

from .store import json_hash


PIPELINE_REVISION = "username-ocr-v1"


class OcrSetupError(RuntimeError):
    """OCR dependencies or models cannot start."""


@dataclass(frozen=True)
class OcrSettings:
    languages: tuple[str, ...] = ("en",)
    sample_interval_seconds: float = 4.0
    maximum_samples: int = 8
    minimum_saved_confidence: float = 0.25  # deprecated, ignored - kept for compat

    def fingerprint(self) -> str:
        # exclude deprecated field so old low-confidence filtering does not affect hash
        d = asdict(self)
        d.pop("minimum_saved_confidence", None)
        return json_hash({"revision": PIPELINE_REVISION, **d})


def sampling_timestamps(duration_seconds: float, interval_seconds: float, maximum_samples: int) -> list[float]:
    if interval_seconds <= 0:
        raise ValueError("sample interval must be greater than zero.")
    if maximum_samples < 1:
        raise ValueError("maximum samples must be at least one.")
    if not math.isfinite(duration_seconds) or duration_seconds <= 0:
        return [0.0]

    sample_count = min(maximum_samples, max(1, math.ceil(duration_seconds / interval_seconds)))
    if sample_count == 1:
        return [round(duration_seconds / 2, 3)]

    margin = min(0.5, duration_seconds * 0.1)
    start = margin
    end = max(start, duration_seconds - margin)
    spacing = (end - start) / (sample_count - 1)
    return [round(start + index * spacing, 3) for index in range(sample_count)]


def progressive_timestamps(
    duration_seconds: float, interval_seconds: float, maximum_samples: int, min_interval: float = 0.25
) -> Iterator[float]:
    seen: set[float] = set()

    for ts in sampling_timestamps(duration_seconds, interval_seconds, maximum_samples):
        seen.add(ts)
        yield ts

    if duration_seconds <= 0:
        return

    margin = min(0.5, duration_seconds * 0.1)
    start = margin
    end = max(start, duration_seconds - margin)

    interval = interval_seconds / 2.0
    while interval >= min_interval:
        count = max(1, math.ceil((end - start) / interval))
        spacing = (end - start) / count if count > 0 else 0
        for index in range(count + 1):
            ts = round(start + index * spacing, 3)
            if not any(abs(ts - s) < 0.1 for s in seen) and ts <= end:
                seen.add(ts)
                yield ts
        interval /= 2.0


class OcrEngine:
    def __init__(self, workspace: Path, force_cpu: bool = False) -> None:
        self._workspace = workspace
        self._force_cpu = force_cpu
        self._reader: Any | None = None
        self._cv2: Any | None = None
        self._numpy: Any | None = None
        self._metadata: dict[str, Any] | None = None

    @property
    def metadata(self) -> dict[str, Any]:
        if self._metadata is None:
            self._initialize(("en",))
        assert self._metadata is not None
        return self._metadata

    def _initialize(self, languages: tuple[str, ...]) -> None:
        if self._reader is not None:
            return
        try:
            import cv2
            import easyocr
            import numpy
            import torch
        except ImportError as error:
            raise OcrSetupError("Missing OCR dependencies. Run: uv sync") from error

        use_gpu = bool(torch.cuda.is_available() and not self._force_cpu)
        try:
            device = torch.cuda.get_device_name(0) if use_gpu else "CPU"
            model_directory = self._workspace / "data" / "easyocr_models"
            model_directory.mkdir(parents=True, exist_ok=True)
            self._reader = easyocr.Reader(
                list(languages),
                gpu=use_gpu,
                model_storage_directory=str(model_directory),
                verbose=False,
            )
        except Exception as error:
            raise OcrSetupError(f"Cannot initialize EasyOCR: {error}") from error

        self._cv2 = cv2
        self._numpy = numpy
        self._metadata = {
            "engine": "easyocr",
            "easyocr_version": getattr(easyocr, "__version__", "unknown"),
            "torch_version": getattr(torch, "__version__", "unknown"),
            "device": device,
            "gpu_enabled": use_gpu,
        }

    def analyze_video(
        self,
        path: Path,
        settings: OcrSettings,
        match_found: Callable[[list[dict[str, Any]]], bool] | None = None,
    ) -> dict[str, Any]:
        self._initialize(settings.languages)
        assert self._cv2 is not None
        assert self._reader is not None

        capture = self._cv2.VideoCapture(str(path))
        if capture.isOpened():
            frames = capture.get(self._cv2.CAP_PROP_FRAME_COUNT)
            fps = capture.get(self._cv2.CAP_PROP_FPS)
            duration = frames / fps if fps and fps > 0 and frames >= 0 else 0.0
        else:
            duration = 0.0
        timestamps = progressive_timestamps(
            duration, settings.sample_interval_seconds, settings.maximum_samples
        )

        observations: list[dict[str, Any]] = []
        decoded_samples = 0
        requested_times: list[float] = []
        try:
            for timestamp in timestamps:
                frame = self._read_frame(capture, path, timestamp)
                if frame is None:
                    continue
                requested_times.append(timestamp)
                decoded_samples += 1
                rows = self._reader.readtext(
                    frame,
                    detail=1,
                    paragraph=False,
                    batch_size=1,
                    workers=0,
                )
                frame_observations: list[dict[str, Any]] = []
                for _bounding_box, text, confidence in rows:
                    cleaned_text = " ".join(str(text).split())
                    numeric_confidence = float(confidence)
                    if not cleaned_text:
                        continue
                    obs = {
                        "seen_at_s": timestamp,
                        "text": cleaned_text,
                        "confidence": round(numeric_confidence, 4),
                    }
                    frame_observations.append(obs)
                    observations.append(obs)
                if match_found and frame_observations and match_found(frame_observations):
                    break
        finally:
            capture.release()

        if decoded_samples == 0:
            raise RuntimeError("Could not decode any requested video frame.")
        return {
            "duration_seconds": round(float(duration), 3),
            "requested_sample_times_s": requested_times,
            "decoded_sample_count": decoded_samples,
            "observations": observations,
            "engine": self.metadata,
        }

    def _read_frame(self, capture: Any, path: Path, timestamp: float) -> Any | None:
        assert self._cv2 is not None
        if capture.isOpened():
            capture.set(self._cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
            success, frame = capture.read()
            if success:
                return frame
        return self._read_frame_with_ffmpeg(path, timestamp)

    def _read_frame_with_ffmpeg(self, path: Path, timestamp: float) -> Any | None:
        assert self._cv2 is not None
        assert self._numpy is not None
        executable = shutil.which("ffmpeg")
        if executable is None:
            return None
        command = [
            executable,
            "-v",
            "error",
            "-ss",
            str(timestamp),
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-f",
            "image2pipe",
            "-vcodec",
            "mjpeg",
            "pipe:1",
        ]
        try:
            result = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if result.returncode != 0 or not result.stdout:
            return None
        return self._cv2.imdecode(
            self._numpy.frombuffer(result.stdout, dtype=self._numpy.uint8), self._cv2.IMREAD_COLOR
        )
