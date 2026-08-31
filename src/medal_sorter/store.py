from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def json_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"sha256:{sha256(encoded.encode('utf-8')).hexdigest()}"


def new_database(medal_root: Path) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "medal_root": str(medal_root.resolve()),
        "records": {},
    }


def get_db(path: Path, medal_root: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS records (clip_path TEXT PRIMARY KEY, data TEXT)")
    conn.execute("INSERT OR REPLACE INTO metadata VALUES (?, ?)", ("schema_version", str(SCHEMA_VERSION)))
    conn.execute("INSERT OR REPLACE INTO metadata VALUES (?, ?)", ("medal_root", str(medal_root.resolve())))
    conn.commit()
    return conn


def _load_sqlite_dict(conn: sqlite3.Connection, medal_root: Path) -> dict[str, Any]:
    cur = conn.cursor()
    cur.execute("SELECT key, value FROM metadata")
    meta = {k: v for k, v in cur.fetchall()}
    # schema check
    sv = meta.get("schema_version")
    if sv is not None and sv != str(SCHEMA_VERSION):
        raise ValueError(f"Unsupported database schema {sv!r}; expected {SCHEMA_VERSION}.")
    cur.execute("SELECT clip_path, data FROM records")
    records = {}
    for clip_path, data in cur.fetchall():
        try:
            records[clip_path] = json.loads(data)
        except json.JSONDecodeError as error:
            raise ValueError(f"Cannot parse record {clip_path}: {error}") from error
    return {
        "schema_version": SCHEMA_VERSION,
        "medal_root": str(medal_root.resolve()),
        "last_discovery_at": meta.get("last_discovery_at"),
        "records": records,
    }


def load_database(path: Path, medal_root: Path) -> dict[str, Any]:
    if not path.exists():
        return new_database(medal_root)
    # detect sqlite by suffix or magic
    if path.suffix.lower() == ".sqlite":
        conn = sqlite3.connect(path)
        try:
            # check if tables exist
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='metadata'")
            if cur.fetchone() is None:
                # empty / legacy json with .sqlite name
                conn.close()
                return new_database(medal_root)
            data = _load_sqlite_dict(conn, medal_root)
            return data
        finally:
            conn.close()
    # legacy json path
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
    if path.suffix.lower() == ".sqlite":
        conn = sqlite3.connect(path)
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)")
            conn.execute("CREATE TABLE IF NOT EXISTS records (clip_path TEXT PRIMARY KEY, data TEXT)")
            conn.execute("INSERT OR REPLACE INTO metadata VALUES (?, ?)", ("schema_version", str(SCHEMA_VERSION)))
            if "medal_root" in value:
                conn.execute("INSERT OR REPLACE INTO metadata VALUES (?, ?)", ("medal_root", str(value["medal_root"])))
            if "last_discovery_at" in value and value["last_discovery_at"] is not None:
                conn.execute("INSERT OR REPLACE INTO metadata VALUES (?, ?)", ("last_discovery_at", str(value["last_discovery_at"])))
            records = value.get("records", {})
            for clip_path, record in records.items():
                conn.execute("INSERT OR REPLACE INTO records VALUES (?, ?)", (clip_path, json.dumps(record)))
            # remove stale records not in dict
            cur = conn.cursor()
            cur.execute("SELECT clip_path FROM records")
            existing = {row[0] for row in cur.fetchall()}
            for stale in existing - set(records.keys()):
                conn.execute("DELETE FROM records WHERE clip_path=?", (stale,))
            conn.commit()
        finally:
            conn.close()
        return
    # legacy json atomic write
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


def db_upsert_record(conn: sqlite3.Connection, clip_path: str, record: dict[str, Any]) -> None:
    conn.execute("INSERT OR REPLACE INTO records VALUES (?, ?)", (clip_path, json.dumps(record)))


def db_fetch_record(conn: sqlite3.Connection, clip_path: str) -> dict[str, Any] | None:
    cur = conn.cursor()
    cur.execute("SELECT data FROM records WHERE clip_path=?", (clip_path,))
    row = cur.fetchone()
    if row is None:
        return None
    return json.loads(row[0])


def db_fetch_all_records(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    cur = conn.cursor()
    cur.execute("SELECT clip_path, data FROM records")
    return {k: json.loads(v) for k, v in cur.fetchall()}


def db_set_metadata(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT OR REPLACE INTO metadata VALUES (?, ?)", (key, value))



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
