from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from .catalog import CatalogSummary, source_records, synchronize_catalog
from .matching import (
    KnownUser,
    default_friends_config,
    load_friends_config,
    resolve_observations,
    resolution_settings_hash,
    has_any_match,
)
from .ocr import OcrEngine, OcrSettings, OcrSetupError, PIPELINE_REVISION
from .store import atomic_write_json, load_database, source_fingerprint, utc_now


DEFAULT_MEDAL_ROOT = Path(r"W:\medal")
DATABASE_FILENAME = "clip_data.json"
FRIENDS_FILENAME = "friends.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="medal-sorter", description="Read-only username extraction for Medal clips."
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    initialize = subcommands.add_parser("init", help="Create local JSON files. No Medal file changes.")
    initialize.add_argument("--workspace", type=Path, default=Path.cwd())

    usernames = subcommands.add_parser("usernames", help="Discover clips and OCR source videos.")
    usernames.add_argument("--workspace", type=Path, default=Path.cwd())
    usernames.add_argument("--medal-root", type=Path, default=DEFAULT_MEDAL_ROOT)
    usernames.add_argument("--game", help="Only analyze one exact Medal game folder name.")
    usernames.add_argument("--limit", type=int, help="Only analyze this many selected source clips.")
    usernames.add_argument("--sample-interval", type=float, default=4.0, metavar="SECONDS")
    usernames.add_argument("--max-samples", type=int, default=8)
    usernames.add_argument(
        "--checkpoint-every",
        type=int,
        default=5,
        metavar="CLIPS",
        help="Atomically save progress after this many clips. Default: 5.",
    )
    usernames.add_argument("--cpu", action="store_true", help="Disable CUDA, even if available.")
    usernames.add_argument(
        "--collect-raw-text",
        action="store_true",
        help="Permit OCR before known users are added. Stores noisy raw OCR only.",
    )
    usernames.add_argument("--force", action="store_true", help="Re-OCR selected source clips.")
    usernames.add_argument("--dry-run", action="store_true", help="Report work. Write nothing.")

    report = subcommands.add_parser("report", help="Print source clips grouped by detected friends.")
    report.add_argument("--workspace", type=Path, default=Path.cwd())
    report.add_argument("--game", help="Only report one exact Medal game folder name.")
    return parser


def _paths(workspace: Path) -> tuple[Path, Path, Path]:
    resolved = workspace.resolve()
    return resolved, resolved / DATABASE_FILENAME, resolved / FRIENDS_FILENAME


def _initialize(workspace: Path) -> int:
    workspace, database_path, friends_path = _paths(workspace)
    created: list[str] = []
    if not database_path.exists():
        atomic_write_json(database_path, load_database(database_path, DEFAULT_MEDAL_ROOT))
        created.append(database_path.name)
    if not friends_path.exists():
        atomic_write_json(friends_path, default_friends_config())
        created.append(friends_path.name)
    if created:
        print(f"Created: {', '.join(created)}")
    else:
        print("Local JSON files already exist.")
    print(f"Edit {friends_path.name}. Add known users. Then run: uv run medal-sorter usernames")
    return 0


def _current_ocr(record: dict[str, Any], settings: OcrSettings) -> bool:
    usernames = record.get("analysis", {}).get("usernames", {})
    ocr = usernames.get("ocr", {}) if isinstance(usernames, dict) else {}
    return (
        ocr.get("state") == "complete"
        and ocr.get("pipeline_revision") == PIPELINE_REVISION
        and ocr.get("settings_hash") == settings.fingerprint()
        and ocr.get("source_fingerprint") == source_fingerprint(record["file"])
    )


def _needs_resolution(record: dict[str, Any], settings_hash: str) -> bool:
    usernames = record.get("analysis", {}).get("usernames", {})
    if not isinstance(usernames, dict):
        return True
    ocr = usernames.get("ocr", {})
    resolution = usernames.get("resolution", {})
    return (
        ocr.get("state") == "complete"
        and (
            resolution.get("state") != "complete"
            or resolution.get("settings_hash") != settings_hash
            or resolution.get("ocr_completed_at") != ocr.get("completed_at")
        )
    )


def _record_ocr_success(
    record: dict[str, Any], result: dict[str, Any], settings: OcrSettings
) -> None:
    usernames = record["analysis"]["usernames"]
    usernames["ocr"] = {
        "state": "complete",
        "pipeline_revision": PIPELINE_REVISION,
        "settings_hash": settings.fingerprint(),
        "source_fingerprint": source_fingerprint(record["file"]),
        "completed_at": utc_now(),
        **result,
    }


def _record_ocr_failure(record: dict[str, Any], error: Exception, settings: OcrSettings) -> None:
    record["analysis"]["usernames"]["ocr"] = {
        "state": "failed",
        "pipeline_revision": PIPELINE_REVISION,
        "settings_hash": settings.fingerprint(),
        "source_fingerprint": source_fingerprint(record["file"]),
        "failed_at": utc_now(),
        "error": str(error),
    }


def _record_resolution(record: dict[str, Any], config: dict[str, Any], users: list[KnownUser]) -> None:
    ocr = record["analysis"]["usernames"]["ocr"]
    resolution = resolve_observations(ocr.get("observations", []), config, users)
    resolution["ocr_completed_at"] = ocr.get("completed_at")
    record["analysis"]["usernames"]["resolution"] = resolution


def _summary_line(summary: CatalogSummary) -> str:
    parts = [
        f"Discovered {summary.source_count} source MP4s, {summary.edit_count} edits",
    ]
    if summary.ignored_count:
        parts.append(f", ignored {summary.ignored_count} extra artifacts")
    parts.append(f". Linked {summary.linked_edit_count}; unresolved edits {summary.unresolved_edit_count}.")
    return "".join(parts)


def _run_usernames(arguments: argparse.Namespace) -> int:
    if arguments.limit is not None and arguments.limit < 1:
        raise ValueError("--limit must be at least 1.")
    if arguments.checkpoint_every < 1:
        raise ValueError("--checkpoint-every must be at least 1.")
    workspace, database_path, friends_path = _paths(arguments.workspace)
    medal_root = arguments.medal_root.resolve()
    if not (medal_root / "Clips").is_dir():
        raise FileNotFoundError(f"Missing Medal Clips directory: {medal_root / 'Clips'}")
    if not friends_path.exists():
        _initialize(workspace)
        print("No OCR run. Add users to friends.json, or use --collect-raw-text.", file=sys.stderr)
        return 2

    database = load_database(database_path, medal_root)
    summary = synchronize_catalog(database, medal_root)
    config, users = load_friends_config(friends_path)
    settings = OcrSettings(
        sample_interval_seconds=arguments.sample_interval,
        maximum_samples=arguments.max_samples,
    )
    resolution_hash = resolution_settings_hash(config, users)
    selected = source_records(database, arguments.game)
    if arguments.limit is not None:
        selected = selected[: arguments.limit]

    current = sum(_current_ocr(record, settings) and not arguments.force for _, record in selected)
    planned = len(selected) - current
    print(_summary_line(summary))
    print(f"Selected source clips: {len(selected)}. OCR current: {current}. OCR planned: {planned}.")
    if arguments.dry_run:
        return 0
    if not users and planned and not arguments.collect_raw_text:
        atomic_write_json(database_path, database)
        print(
            "No known users in friends.json. Add mappings first, or rerun with --collect-raw-text.",
            file=sys.stderr,
        )
        return 2

    atomic_write_json(database_path, database)
    engine: OcrEngine | None = None
    analyzed = 0
    failed = 0
    resolved = 0
    interrupted = False
    try:
        for index, (key, record) in enumerate(selected, start=1):
            requires_ocr = arguments.force or not _current_ocr(record, settings)
            if requires_ocr:
                source_path = medal_root / Path(record["path"])
                print(f"[{index}/{len(selected)}] OCR {record['path']}")
                if engine is None:
                    engine = OcrEngine(workspace, force_cpu=arguments.cpu)
                try:
                    def match_found(new_obs: list[dict[str, Any]]) -> bool:
                        return has_any_match(new_obs, config, users)

                    result = engine.analyze_video(source_path, settings, match_found)
                except OcrSetupError:
                    atomic_write_json(database_path, database)
                    raise
                except KeyboardInterrupt:
                    # Mid-OCR interrupt: do NOT mark this clip as done.
                    print(f"\n  interrupted during OCR — clip not saved.", file=sys.stderr)
                    interrupted = True
                    break
                except Exception as error:
                    _record_ocr_failure(record, error, settings)
                    failed += 1
                    print(f"  failed: {error}", file=sys.stderr)
                else:
                    _record_ocr_success(record, result, settings)
                    _record_resolution(record, config, users)
                    analyzed += 1
                    resolved += 1
                    obs_count = len(result.get("observations", []))
                    samples = result.get("decoded_sample_count", 0)
                    print(f"  done: {obs_count} text observations from {samples} frames")

            if _needs_resolution(record, resolution_hash):
                _record_resolution(record, config, users)
                resolved += 1
            if index % arguments.checkpoint_every == 0:
                atomic_write_json(database_path, database)
    except KeyboardInterrupt:
        # Interrupt between clips (not during OCR).
        interrupted = True
        print("\nInterrupted.", file=sys.stderr)

    atomic_write_json(database_path, database)
    device = engine.metadata["device"] if engine is not None else "not needed"
    if interrupted:
        print(
            f"Saved progress. OCR analyzed {analyzed}; failed {failed}; "
            f"resolution refreshed {resolved}; device {device}."
        )
        return 130
    print(
        f"Done. OCR analyzed {analyzed}; failed {failed}; resolution refreshed {resolved}; device {device}."
    )
    return 1 if failed else 0


def _run_report(arguments: argparse.Namespace) -> int:
    _, database_path, _ = _paths(arguments.workspace)
    if not database_path.exists():
        raise FileNotFoundError(f"Missing local database: {database_path}. Run init, then usernames.")
    database = load_database(database_path, DEFAULT_MEDAL_ROOT)
    grouped: dict[str, list[tuple[str, list[str]]]] = {}
    matched_source_count = 0
    for _, record in source_records(database, arguments.game):
        usernames = record.get("analysis", {}).get("usernames", {})
        resolution = usernames.get("resolution", {}) if isinstance(usernames, dict) else {}
        matches = resolution.get("usernames", []) if isinstance(resolution, dict) else []
        if not isinstance(matches, list) or not matches:
            continue
        matched_source_count += 1
        by_group: dict[str, list[str]] = {}
        for match in matches:
            if not isinstance(match, dict) or not isinstance(match.get("name"), str):
                continue
            group = match.get("group") if isinstance(match.get("group"), str) else "(ungrouped)"
            by_group.setdefault(group, []).append(match["name"])
        for group, names in by_group.items():
            grouped.setdefault(group, []).append((record["path"], sorted(names, key=str.casefold)))

    if not grouped:
        print("No matched usernames in selected source clips.")
        return 0
    print(f"Matched source clips: {matched_source_count}")
    for group in sorted(grouped, key=str.casefold):
        print(f"\n[{group}]")
        for path, names in sorted(grouped[group], key=lambda item: item[0].casefold()):
            print(f"{path} :: {', '.join(names)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "init":
            return _initialize(arguments.workspace)
        if arguments.command == "usernames":
            return _run_usernames(arguments)
        if arguments.command == "report":
            return _run_report(arguments)
    except (FileNotFoundError, OcrSetupError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    raise AssertionError(f"Unhandled command: {arguments.command}")


if __name__ == "__main__":
    raise SystemExit(main())
