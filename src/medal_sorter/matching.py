from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rapidfuzz.distance import Levenshtein

from .store import json_hash, utc_now


RESOLUTION_REVISION = "username-resolution-v1"


@dataclass(frozen=True)
class KnownUser:
    name: str
    group: str | None
    aliases: tuple[str, ...]


def default_friends_config() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "ignored_ocr_text": [],
        "matching": {
            "fuzzy_threshold": 90,
        },
        "groups": {},
    }


def load_friends_config(path: Path) -> tuple[dict[str, Any], list[KnownUser]]:
    config = _load_friends_config(path)
    if not isinstance(config, dict):
        raise ValueError(f"Friend config {path} must contain a YAML mapping.")
    if config.get("schema_version") != 1:
        raise ValueError("Friend config schema_version must be 1.")

    raw_users = config.get("users")
    if raw_users is None:
        raw_users = _users_from_group_sections(config)
        config = {**config, "users": raw_users}
    if not isinstance(raw_users, list):
        raise ValueError("Friend config users must be an array.")

    users: list[KnownUser] = []
    names_seen: set[str] = set()
    for index, raw_user in enumerate(raw_users):
        if not isinstance(raw_user, dict):
            raise ValueError(f"users[{index}] must be an object.")
        name = raw_user.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"users[{index}].name must be a non-empty string.")
        normalized_name = normalize(name)
        if len(normalized_name) < 3:
            raise ValueError(f"users[{index}].name must contain at least 3 letters or digits.")
        if normalized_name in names_seen:
            raise ValueError(f"Duplicate user name: {name!r}")
        names_seen.add(normalized_name)

        group = raw_user.get("group")
        if group is not None and (not isinstance(group, str) or not group.strip()):
            raise ValueError(f"users[{index}].group must be a non-empty string when supplied.")
        aliases = raw_user.get("aliases", [])
        if not isinstance(aliases, list) or not all(isinstance(alias, str) for alias in aliases):
            raise ValueError(f"users[{index}].aliases must be an array of strings.")
        aliases = tuple(alias.strip() for alias in aliases if normalize(alias))
        users.append(KnownUser(name=name.strip(), group=group.strip() if group else None, aliases=aliases))

    return config, users


def _load_friends_config(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    if suffix not in {".yaml", ".yml"}:
        raise ValueError("Friend config must use a .yaml or .yml extension.")
    try:
        import yaml
    except ImportError as error:
        raise RuntimeError("PyYAML is required to parse YAML friend config files.") from error
    try:
        return yaml.safe_load(text)
    except Exception as error:
        raise ValueError(f"Cannot parse friend config {path}: {error}") from error


def _users_from_group_sections(config: dict[str, Any]) -> list[dict[str, Any]]:
    known_keys = {"schema_version", "users", "groups", "ignored_ocr_text", "matching", "example_user"}
    groups = config.get("groups")
    if groups is None:
        groups = {key: value for key, value in config.items() if key not in known_keys}
    if not groups:
        return []
    if not isinstance(groups, dict):
        raise ValueError("Friend config groups must be a mapping of group names to username lists.")

    users: list[dict[str, Any]] = []
    seen: set[str] = set()

    for group_name, entries in groups.items():
        if not isinstance(group_name, str) or not group_name.strip():
            raise ValueError("Friend config group names must be non-empty strings.")
        if not isinstance(entries, list):
            raise ValueError(f"group {group_name!r} must contain a list of usernames.")

        normalized_group = group_name.strip()
        for entry_index, entry in enumerate(entries):
            if isinstance(entry, str):
                name = entry.strip()
                if not name:
                    raise ValueError(f"groups[{group_name}][{entry_index}] contains an empty name.")
                normalized_name = normalize(name)
                if not normalized_name:
                    continue
                if normalized_name in seen:
                    continue
                seen.add(normalized_name)
                users.append({"name": name, "group": normalized_group, "aliases": []})
                continue

            if isinstance(entry, dict) and len(entry) == 1:
                canonical_name, alias_values = next(iter(entry.items()))
                if not isinstance(canonical_name, str) or not canonical_name.strip():
                    raise ValueError(f"groups[{group_name}][{entry_index}] has an invalid canonical name.")
                if isinstance(alias_values, str):
                    alias_values = [alias_values]
                elif isinstance(alias_values, list) and all(isinstance(item, str) for item in alias_values):
                    alias_values = [item for item in alias_values if item.strip()]
                else:
                    raise ValueError(
                        f"groups[{group_name}][{entry_index}] must map a name to a string or list of strings."
                    )

                for name in [canonical_name, *alias_values]:
                    if not isinstance(name, str) or not name.strip():
                        raise ValueError(f"groups[{group_name}][{entry_index}] contains an invalid name.")
                    normalized_name = normalize(name)
                    if not normalized_name:
                        continue
                    if normalized_name in seen:
                        continue
                    seen.add(normalized_name)
                    users.append({"name": name.strip(), "group": normalized_group, "aliases": []})
                continue

            raise ValueError(
                f"groups[{group_name}][{entry_index}] must be a string or single-key mapping."
            )

    return users


def normalize(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def resolution_settings_hash(config: dict[str, Any], users: list[KnownUser]) -> str:
    matching = config.get("matching") if isinstance(config.get("matching"), dict) else {}
    value = {
        "revision": RESOLUTION_REVISION,
        "users": [
            {"name": user.name, "group": user.group, "aliases": list(user.aliases)} for user in users
        ],
        "ignored_ocr_text": config.get("ignored_ocr_text", []),
        "fuzzy_threshold": matching.get("fuzzy_threshold", 90),
    }
    return json_hash(value)


def _config_number(config: dict[str, Any], name: str, default: float) -> float:
    matching = config.get("matching") if isinstance(config.get("matching"), dict) else {}
    value = matching.get(name, default)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"matching.{name} must be a number.")
    return float(value)


def _best_match_score(observed_text: str, target: str) -> float:
    """Fast fuzzy match between observed OCR text and a target username/alias.
    
    Uses exact matching for short names and normalized Levenshtein distance for longer ones.
    """
    normalized_target = normalize(target)
    if not normalized_target:
        return 0.0
    
    normalized_obs = normalize(observed_text)
    if normalized_obs == normalized_target:
        return 1.0
    
    # For short targets (<=4 chars), only exact match counts
    if len(normalized_target) <= 4:
        return 0.0
    
    # Try the full normalized text first (best case)
    if normalized_obs:
        score = Levenshtein.normalized_similarity(normalized_obs, normalized_target)
        if score == 1.0:
            return 1.0
    
    # For longer targets, also try substring/token matches
    tokens = [normalize(token) for token in re.findall(r"[^\W_]+", observed_text, flags=re.UNICODE)]
    tokens = [token for token in tokens if token]
    
    max_score = 0.0
    for index in range(len(tokens)):
        for width in range(1, min(4, len(tokens) - index) + 1):
            candidate = "".join(tokens[index : index + width])
            if candidate == normalized_target:
                return 1.0
            score = Levenshtein.normalized_similarity(candidate, normalized_target)
            if score > max_score:
                max_score = score
    
    return max_score


def has_any_match(
    observations: list[dict[str, Any]], config: dict[str, Any], users: list[KnownUser]
) -> bool:
    fuzzy_threshold = _config_number(config, "fuzzy_threshold", 90) / 100.0

    ignored = {
        normalize(item)
        for item in config.get("ignored_ocr_text", [])
        if isinstance(item, str) and normalize(item)
    }

    for observation in observations:
        text = observation.get("text")
        confidence = observation.get("confidence")
        if not isinstance(text, str) or not isinstance(confidence, (int, float)):
            continue
        if normalize(text) in ignored:
            continue

        for user in users:
            aliases = (user.name, *user.aliases)
            # Find best match score across all aliases
            best_score = max((_best_match_score(text, alias) for alias in aliases), default=0.0)
            if best_score >= fuzzy_threshold:
                return True
    return False


def resolve_observations(
    observations: list[dict[str, Any]], config: dict[str, Any], users: list[KnownUser]
) -> dict[str, Any]:
    fuzzy_threshold = _config_number(config, "fuzzy_threshold", 90) / 100.0
    if not 0 <= fuzzy_threshold <= 1:
        raise ValueError("matching.fuzzy_threshold must be from 0 through 100.")

    ignored = {
        normalize(item)
        for item in config.get("ignored_ocr_text", [])
        if isinstance(item, str) and normalize(item)
    }
    resolved: dict[str, dict[str, Any]] = {}

    for observation in observations:
        text = observation.get("text")
        confidence = observation.get("confidence")
        if not isinstance(text, str) or not isinstance(confidence, (int, float)):
            continue
        if normalize(text) in ignored:
            continue

        for user in users:
            aliases = (user.name, *user.aliases)
            # Calculate scores for all aliases at once
            alias_scores = [(alias, _best_match_score(text, alias)) for alias in aliases]
            best_alias, score = max(alias_scores, key=lambda x: x[1])
            
            if score < fuzzy_threshold:
                continue

            evidence = {
                "matched_text": text,
                "alias": best_alias,
                "ocr_confidence": round(float(confidence), 4),
                "match_score": round(score, 4),
                "confidence": round(float(confidence) * score, 4),
                "seen_at_s": observation.get("seen_at_s"),
            }
            entry = resolved.setdefault(
                user.name,
                {
                    "name": user.name,
                    "group": user.group,
                    "best_confidence": 0.0,
                    "occurrences": 0,
                    "seen_at_s": [],
                    "evidence": [],
                },
            )
            entry["occurrences"] += 1
            entry["best_confidence"] = max(entry["best_confidence"], evidence["confidence"])
            seen_at = evidence["seen_at_s"]
            if isinstance(seen_at, (int, float)) and seen_at not in entry["seen_at_s"]:
                entry["seen_at_s"].append(seen_at)
            if len(entry["evidence"]) < 12:
                entry["evidence"].append(evidence)

    usernames = sorted(resolved.values(), key=lambda user: (-user["best_confidence"], user["name"].casefold()))
    for username in usernames:
        username["seen_at_s"].sort()
        username["best_confidence"] = round(username["best_confidence"], 4)

    return {
        "state": "complete",
        "revision": RESOLUTION_REVISION,
        "settings_hash": resolution_settings_hash(config, users),
        "completed_at": utc_now(),
        "usernames": usernames,
    }
