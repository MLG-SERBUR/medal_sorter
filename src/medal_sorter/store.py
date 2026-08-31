from __future__ import annotations

import json
import sqlite3
import tempfile
import os
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


def get_db(path: Path, medal_root: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute('CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)')
    conn.execute('CREATE TABLE IF NOT EXISTS records (clip_path TEXT PRIMARY KEY, data TEXT)')
    
    conn.execute('INSERT OR REPLACE INTO metadata VALUES (?, ?)', ('schema_version', str(SCHEMA_VERSION)))
    conn.execute('INSERT OR REPLACE INTO metadata VALUES (?, ?)', ('medal_root', str(medal_root.resolve())))
    conn.commit()
    return conn



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
