from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def json_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"sha256:{sha256(encoded.encode('utf-8')).hexdigest()}"


def new_database(medal_root: Path) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "medal_root": str(medal_root.resolve()),
        "records": {},
    }


def load_database(path: Path, medal_root: Path) -> dict[str, Any]:
    if not path.exists():
        return new_database(medal_root)

    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"Cannot parse database {path}: {error}") from error

    if not isinstance(loaded, dict):
        raise ValueError(f"Database {path} must contain a JSON object.")
    if loaded.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported database schema {loaded.get('schema_version')!r}; expected {SCHEMA_VERSION}."
        )
    if not isinstance(loaded.get("records"), dict):
        raise ValueError(f"Database {path} has no valid records object.")

    loaded["medal_root"] = str(medal_root.resolve())
    return loaded


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n"

    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, text=True
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="\n") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def atomic_write_yaml(path: Path, value: dict[str, Any]) -> None:
    try:
        import yaml
    except ImportError as error:
        raise RuntimeError("PyYAML is required to write YAML friend config files.") from error

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = yaml.safe_dump(value, sort_keys=False, allow_unicode=True)
    if not payload.endswith("\n"):
        payload += "\n"

    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, text=True
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="\n") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def relative_key(path: Path, medal_root: Path) -> str:
    return path.resolve().relative_to(medal_root.resolve()).as_posix().casefold()


def relative_display_path(path: Path, medal_root: Path) -> str:
    return path.resolve().relative_to(medal_root.resolve()).as_posix()


def file_details(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "last_seen_at": utc_now(),
    }


def source_fingerprint(file_record: dict[str, Any]) -> dict[str, int]:
    return {
        "size_bytes": int(file_record["size_bytes"]),
        "mtime_ns": int(file_record["mtime_ns"]),
    }
