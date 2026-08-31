from __future__ import annotations

import json
import sqlite3
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .store import file_details, relative_display_path, relative_key, utc_now


VIDEO_SUFFIXES = {".mp4"}
EDIT_MARKER = re.compile(r"(?i)(?:--edit|--tr-edit|-tr-edit|-trim-[0-9]+)")
IGNORED_PATTERNS = (
    (re.compile(r"(?i)_(discord|game|mic)$"), "medal_editor_audio_export"),
    (re.compile(r"(?i)^temp_"), "temp_file"),
    (re.compile(r"(?i)^uncapped_"), "uncapped_script_export"),
    (re.compile(r"(?i)-merged-"), "losslesscut_merge_export"),
    (re.compile(r"(?i)-cut"), "losslesscut_cut_export"),
    (re.compile(r"^-[0-9]+$"), "temp_file"),
    (re.compile(r"(?i)(?<!trim)(?<!edit)-[0-9]{9,}$"), "external_timestamp_cut"),
)


@dataclass(frozen=True)
class CatalogSummary:
    source_count: int
    edit_count: int
    ignored_count: int
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


def _ignored_analysis() -> dict[str, Any]:
    return {
        "usernames": {"state": "ignored"},
        "clip_type": None,
        "trim": None,
        "audio_channels": None,
    }


def _is_ignored(path: Path) -> str | None:
    """Return the reason if path is an extra/ignored file (audio exports, temp, scripts), else None."""
    stem = path.stem
    for pattern, reason in IGNORED_PATTERNS:
        if pattern.search(stem):
            return reason
    return None


def _ensure_source_record(
    record: dict[str, Any] | None, path: Path, medal_root: Path
) -> tuple[str, dict[str, Any]]:
    key = relative_key(path, medal_root)
    if not isinstance(record, dict) or record.get("kind") != "source":
        record = {
            "kind": "source",
            "path": relative_display_path(path, medal_root),
            "file": file_details(path),
            "links": {"edits": []},
            "analysis": _source_analysis(),
        }
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
    record: dict[str, Any] | None, path: Path, medal_root: Path
) -> tuple[str, dict[str, Any]]:
    key = relative_key(path, medal_root)
    if not isinstance(record, dict) or record.get("kind") != "edit":
        record = {
            "kind": "edit",
            "path": relative_display_path(path, medal_root),
            "file": file_details(path),
            "analysis": _edit_analysis(),
        }
    else:
        record["path"] = relative_display_path(path, medal_root)
        record["file"] = file_details(path)
        record.setdefault("analysis", _edit_analysis())
        record["analysis"].setdefault("usernames", {"state": "unresolved"})
        record["analysis"].setdefault("clip_type", None)
        record["analysis"].setdefault("trim", None)
        record["analysis"].setdefault("audio_channels", None)
    return key, record


def _ensure_ignored_record(
    record: dict[str, Any] | None, path: Path, medal_root: Path, reason: str
) -> tuple[str, dict[str, Any]]:
    key = relative_key(path, medal_root)
    if not isinstance(record, dict) or record.get("kind") != "ignored":
        record = {
            "kind": "ignored",
            "reason": reason,
            "path": relative_display_path(path, medal_root),
            "file": file_details(path),
            "analysis": _ignored_analysis(),
        }
    else:
        record["path"] = relative_display_path(path, medal_root)
        record["file"] = file_details(path)
        record["reason"] = reason
        record.setdefault("analysis", _ignored_analysis())
    return key, record


def _parent_stem(edit_path: Path) -> str | None:
    marker = EDIT_MARKER.search(edit_path.stem)
    if marker is None or marker.start() == 0:
        return None
    return edit_path.stem[: marker.start()]


def _is_sqlite_conn(obj: Any) -> bool:
    return isinstance(obj, sqlite3.Connection)


def synchronize_catalog(database: dict[str, Any] | sqlite3.Connection, medal_root: Path) -> CatalogSummary:
    clips_root = medal_root / "Clips"
    edits_root = medal_root / "Edits"
    if not clips_root.is_dir():
        raise FileNotFoundError(f"Missing Medal Clips directory: {clips_root}")

    if _is_sqlite_conn(database):
        conn: sqlite3.Connection = database  # type: ignore
        conn.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS records (clip_path TEXT PRIMARY KEY, data TEXT)")
        cur = conn.cursor()
        cur.execute("SELECT clip_path, data FROM records")
        records: dict[str, dict[str, Any]] = {}
        for clip_path, data in cur.fetchall():
            try:
                records[clip_path] = json.loads(data)
            except json.JSONDecodeError:
                continue
        for rec in records.values():
            if isinstance(rec, dict) and rec.get("kind") in {"source", "edit", "ignored"}:
                rec["present"] = False

        source_paths = _videos_in(clips_root)
        source_stems: dict[str, list[str]] = {}
        current_sources: dict[str, dict[str, Any]] = {}
        ignored_count = 0

        for path in source_paths:
            ignored_reason = _is_ignored(path)
            key = relative_key(path, medal_root)
            existing = records.get(key)
            if ignored_reason is not None:
                ig_key, ig_record = _ensure_ignored_record(existing, path, medal_root, ignored_reason)
                ig_record["present"] = True
                records[ig_key] = ig_record
                ignored_count += 1
                continue
            s_key, s_record = _ensure_source_record(existing, path, medal_root)
            s_record["present"] = True
            s_record["links"]["edits"] = []
            records[s_key] = s_record
            current_sources[s_key] = s_record
            source_stems.setdefault(path.stem.casefold(), []).append(s_key)

        linked = 0
        unresolved = 0
        edit_paths = _videos_in(edits_root)
        actual_edits = 0
        for path in edit_paths:
            ignored_reason = _is_ignored(path)
            key = relative_key(path, medal_root)
            existing = records.get(key)
            if ignored_reason is not None:
                ig_key, ig_record = _ensure_ignored_record(existing, path, medal_root, ignored_reason)
                ig_record["present"] = True
                records[ig_key] = ig_record
                ignored_count += 1
                continue
            actual_edits += 1
            e_key, e_record = _ensure_edit_record(existing, path, medal_root)
            e_record["present"] = True
            parent_stem = _parent_stem(path)
            candidates = source_stems.get(parent_stem.casefold(), []) if parent_stem else []
            if len(candidates) == 1:
                source_key = candidates[0]
                e_record["source"] = {
                    "state": "linked",
                    "record_key": source_key,
                    "method": "filename_marker_exact",
                }
                e_record["analysis"]["usernames"] = {
                    "state": "inherited",
                    "from": source_key,
                }
                current_sources[source_key]["links"]["edits"].append(e_key)
                linked += 1
            else:
                e_record["source"] = {
                    "state": "unresolved",
                    "candidate_stem": parent_stem,
                    "candidates": candidates,
                }
                e_record["analysis"]["usernames"] = {"state": "unresolved"}
                unresolved += 1
            records[e_key] = e_record

        for src in current_sources.values():
            src["links"]["edits"].sort()

        now = utc_now()
        conn.execute("INSERT OR REPLACE INTO metadata VALUES (?, ?)", ("medal_root", str(medal_root.resolve())))
        conn.execute("INSERT OR REPLACE INTO metadata VALUES (?, ?)", ("last_discovery_at", now))
        for clip_path, rec in records.items():
            conn.execute("INSERT OR REPLACE INTO records VALUES (?, ?)", (clip_path, json.dumps(rec)))
        # commit handled by caller (cli) so dry-run can rollback
        return CatalogSummary(
            source_count=len(current_sources),
            edit_count=actual_edits,
            ignored_count=ignored_count,
            linked_edit_count=linked,
            unresolved_edit_count=unresolved,
        )

    # legacy dict path (for tests)
    records = database["records"]  # type: ignore
    database["medal_root"] = str(medal_root.resolve())  # type: ignore

    for record in records.values():
        if isinstance(record, dict) and record.get("kind") in {"source", "edit", "ignored"}:
            record["present"] = False

    source_paths = _videos_in(clips_root)
    source_stems: dict[str, list[str]] = {}
    current_sources: dict[str, dict[str, Any]] = {}
    ignored_count = 0

    for path in source_paths:
        ignored_reason = _is_ignored(path)
        key = relative_key(path, medal_root)
        existing = records.get(key)
        if ignored_reason is not None:
            ig_key, ig_record = _ensure_ignored_record(existing, path, medal_root, ignored_reason)
            ig_record["present"] = True
            records[ig_key] = ig_record
            ignored_count += 1
            continue
        s_key, s_record = _ensure_source_record(existing, path, medal_root)
        s_record["present"] = True
        s_record["links"]["edits"] = []
        records[s_key] = s_record
        current_sources[s_key] = s_record
        source_stems.setdefault(path.stem.casefold(), []).append(s_key)

    linked = 0
    unresolved = 0
    edit_paths = _videos_in(edits_root)
    actual_edits = 0
    for path in edit_paths:
        ignored_reason = _is_ignored(path)
        key = relative_key(path, medal_root)
        existing = records.get(key)
        if ignored_reason is not None:
            ig_key, ig_record = _ensure_ignored_record(existing, path, medal_root, ignored_reason)
            ig_record["present"] = True
            records[ig_key] = ig_record
            ignored_count += 1
            continue
        actual_edits += 1
        e_key, e_record = _ensure_edit_record(existing, path, medal_root)
        e_record["present"] = True
        parent_stem = _parent_stem(path)
        candidates = source_stems.get(parent_stem.casefold(), []) if parent_stem else []
        if len(candidates) == 1:
            source_key = candidates[0]
            e_record["source"] = {
                "state": "linked",
                "record_key": source_key,
                "method": "filename_marker_exact",
            }
            e_record["analysis"]["usernames"] = {
                "state": "inherited",
                "from": source_key,
            }
            current_sources[source_key]["links"]["edits"].append(e_key)
            linked += 1
        else:
            e_record["source"] = {
                "state": "unresolved",
                "candidate_stem": parent_stem,
                "candidates": candidates,
            }
            e_record["analysis"]["usernames"] = {"state": "unresolved"}
            unresolved += 1
        records[e_key] = e_record

    for source_record in current_sources.values():
        source_record["links"]["edits"].sort()

    database["last_discovery_at"] = utc_now()  # type: ignore
    return CatalogSummary(
        source_count=len(current_sources),
        edit_count=actual_edits,
        ignored_count=ignored_count,
        linked_edit_count=linked,
        unresolved_edit_count=unresolved,
    )


def source_records(
    database: dict[str, Any] | sqlite3.Connection, game: str | None = None
) -> list[tuple[str, dict[str, Any]]]:
    selected: list[tuple[str, dict[str, Any]]] = []
    wanted_game = game.casefold() if game else None
    if _is_sqlite_conn(database):
        conn: sqlite3.Connection = database  # type: ignore
        cur = conn.cursor()
        cur.execute("SELECT clip_path, data FROM records")
        for clip_path, data in cur.fetchall():
            try:
                record = json.loads(data)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict) or record.get("kind") != "source":
                continue
            if not record.get("present", True):
                continue
            path = str(record.get("path", ""))
            parts = path.split("/")
            if wanted_game and (len(parts) < 3 or parts[1].casefold() != wanted_game):
                continue
            selected.append((clip_path, record))
        return sorted(selected, key=lambda item: item[0])

    for key, record in database["records"].items():  # type: ignore
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
