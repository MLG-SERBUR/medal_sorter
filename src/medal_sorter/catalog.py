from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .store import file_details, relative_display_path, relative_key, utc_now


VIDEO_SUFFIXES = {".mp4"}
EDIT_MARKER = re.compile(r"(?i)(?:--edit|--tr-edit|-tr-edit|-trim-[0-9]+)")
AUDIO_TRACK_SUFFIX = re.compile(r"(?i)_(discord|game|mic)$")


@dataclass(frozen=True)
class CatalogSummary:
    source_count: int
    edit_count: int
    audio_track_count: int
    linked_edit_count: int
    unresolved_edit_count: int


def _videos_in(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(
        (
            path
            for path in directory.rglob("*")
            if path.is_file() and path.suffix.casefold() in VIDEO_SUFFIXES
        ),
        key=lambda path: str(path).casefold(),
    )


def _source_analysis() -> dict[str, Any]:
    return {
        "usernames": {
            "ocr": {"state": "not_started"},
            "resolution": {"state": "not_started"},
        },
        "clip_type": None,
        "trim": None,
        "audio_channels": None,
    }


def _edit_analysis() -> dict[str, Any]:
    return {
        "usernames": {"state": "unresolved"},
        "clip_type": None,
        "trim": None,
        "audio_channels": None,
    }


def _audio_track_analysis() -> dict[str, Any]:
    return {
        "usernames": {"state": "not_applicable"},
        "clip_type": None,
        "trim": None,
        "audio_channels": None,
    }


def _is_audio_track(path: Path) -> str | None:
    """Return the track kind (discord/game/mic) if path is an audio-only track, else None."""
    match = AUDIO_TRACK_SUFFIX.search(path.stem)
    return match.group(1).casefold() if match else None


def _audio_track_parent_stem(path: Path) -> str | None:
    """Return the stem of the parent clip for an audio track file."""
    match = AUDIO_TRACK_SUFFIX.search(path.stem)
    if match is None or match.start() == 0:
        return None
    return path.stem[: match.start()]


def _ensure_source_record(
    records: dict[str, Any], path: Path, medal_root: Path
) -> tuple[str, dict[str, Any]]:
    key = relative_key(path, medal_root)
    record = records.get(key)
    if not isinstance(record, dict) or record.get("kind") != "source":
        record = {
            "kind": "source",
            "path": relative_display_path(path, medal_root),
            "file": file_details(path),
            "links": {"edits": []},
            "analysis": _source_analysis(),
        }
        records[key] = record
    else:
        record["path"] = relative_display_path(path, medal_root)
        record["file"] = file_details(path)
        record.setdefault("links", {}).setdefault("edits", [])
        record.setdefault("analysis", _source_analysis())
        record["analysis"].setdefault("usernames", _source_analysis()["usernames"])
        record["analysis"].setdefault("clip_type", None)
        record["analysis"].setdefault("trim", None)
        record["analysis"].setdefault("audio_channels", None)
    return key, record


def _ensure_edit_record(
    records: dict[str, Any], path: Path, medal_root: Path
) -> tuple[str, dict[str, Any]]:
    key = relative_key(path, medal_root)
    record = records.get(key)
    if not isinstance(record, dict) or record.get("kind") != "edit":
        record = {
            "kind": "edit",
            "path": relative_display_path(path, medal_root),
            "file": file_details(path),
            "analysis": _edit_analysis(),
        }
        records[key] = record
    else:
        record["path"] = relative_display_path(path, medal_root)
        record["file"] = file_details(path)
        record.setdefault("analysis", _edit_analysis())
        record["analysis"].setdefault("usernames", {"state": "unresolved"})
        record["analysis"].setdefault("clip_type", None)
        record["analysis"].setdefault("trim", None)
        record["analysis"].setdefault("audio_channels", None)
    return key, record


def _ensure_audio_track_record(
    records: dict[str, Any], path: Path, medal_root: Path, track_kind: str,
) -> tuple[str, dict[str, Any]]:
    key = relative_key(path, medal_root)
    record = records.get(key)
    if not isinstance(record, dict) or record.get("kind") != "audio_track":
        record = {
            "kind": "audio_track",
            "track_kind": track_kind,
            "path": relative_display_path(path, medal_root),
            "file": file_details(path),
            "analysis": _audio_track_analysis(),
        }
        records[key] = record
    else:
        record["path"] = relative_display_path(path, medal_root)
        record["file"] = file_details(path)
        record["track_kind"] = track_kind
        record.setdefault("analysis", _audio_track_analysis())
    return key, record


def _parent_stem(edit_path: Path) -> str | None:
    marker = EDIT_MARKER.search(edit_path.stem)
    if marker is None or marker.start() == 0:
        return None
    return edit_path.stem[: marker.start()]


def synchronize_catalog(database: dict[str, Any], medal_root: Path) -> CatalogSummary:
    clips_root = medal_root / "Clips"
    edits_root = medal_root / "Edits"
    if not clips_root.is_dir():
        raise FileNotFoundError(f"Missing Medal Clips directory: {clips_root}")

    records = database["records"]
    database["medal_root"] = str(medal_root.resolve())

    # Retain records for files Medal no longer has, but never send those stale
    # records back through OCR on a later run.
    for record in records.values():
        if isinstance(record, dict) and record.get("kind") in {"source", "edit"}:
            record["present"] = False

    source_paths = _videos_in(clips_root)
    source_stems: dict[str, list[str]] = {}
    current_sources: dict[str, dict[str, Any]] = {}
    audio_track_count = 0
    for path in source_paths:
        track_kind = _is_audio_track(path)
        if track_kind is not None:
            # Audio-only channel export (e.g. _discord.mp4). Link to parent, skip OCR.
            at_key, at_record = _ensure_audio_track_record(records, path, medal_root, track_kind)
            at_record["present"] = True
            parent_stem = _audio_track_parent_stem(path)
            parent_candidates = source_stems.get(parent_stem.casefold(), []) if parent_stem else []
            if len(parent_candidates) == 1:
                at_record["source"] = {"state": "linked", "record_key": parent_candidates[0]}
                current_sources[parent_candidates[0]]["links"].setdefault("audio_tracks", []).append(at_key)
            else:
                at_record["source"] = {
                    "state": "unresolved",
                    "candidate_stem": parent_stem,
                    "candidates": parent_candidates,
                }
            audio_track_count += 1
            continue
        key, record = _ensure_source_record(records, path, medal_root)
        record["present"] = True
        record["links"]["edits"] = []
        record["links"].setdefault("audio_tracks", [])
        current_sources[key] = record
        source_stems.setdefault(path.stem.casefold(), []).append(key)

    linked = 0
    unresolved = 0
    edit_paths = _videos_in(edits_root)
    for path in edit_paths:
        edit_key, edit_record = _ensure_edit_record(records, path, medal_root)
        edit_record["present"] = True
        parent_stem = _parent_stem(path)
        candidates = source_stems.get(parent_stem.casefold(), []) if parent_stem else []

        if len(candidates) == 1:
            source_key = candidates[0]
            edit_record["source"] = {
                "state": "linked",
                "record_key": source_key,
                "method": "filename_marker_exact",
            }
            edit_record["analysis"]["usernames"] = {
                "state": "inherited",
                "from": source_key,
            }
            current_sources[source_key]["links"]["edits"].append(edit_key)
            linked += 1
        else:
            edit_record["source"] = {
                "state": "unresolved",
                "candidate_stem": parent_stem,
                "candidates": candidates,
            }
            edit_record["analysis"]["usernames"] = {"state": "unresolved"}
            unresolved += 1

    for source_record in current_sources.values():
        source_record["links"]["edits"].sort()
        if "audio_tracks" in source_record["links"]:
            source_record["links"]["audio_tracks"].sort()
    database["last_discovery_at"] = utc_now()
    return CatalogSummary(
        source_count=len(source_paths) - audio_track_count,
        edit_count=len(edit_paths),
        audio_track_count=audio_track_count,
        linked_edit_count=linked,
        unresolved_edit_count=unresolved,
    )


def source_records(
    database: dict[str, Any], game: str | None = None
) -> list[tuple[str, dict[str, Any]]]:
    selected: list[tuple[str, dict[str, Any]]] = []
    wanted_game = game.casefold() if game else None
    for key, record in database["records"].items():
        if not isinstance(record, dict) or record.get("kind") != "source":
            continue
        if not record.get("present", True):
            continue
        path = str(record.get("path", ""))
        parts = path.split("/")
        if wanted_game and (len(parts) < 3 or parts[1].casefold() != wanted_game):
            continue
        selected.append((key, record))
    return sorted(selected, key=lambda item: item[0])
