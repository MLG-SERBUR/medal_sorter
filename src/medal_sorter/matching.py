from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rapidfuzz.fuzz import ratio

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
        "users": [],
        "example_user": {
            "name": "PlayerDisplayName",
            "group": "Friday friends",
            "aliases": ["InGameHandle"],
        },
        "ignored_ocr_text": [],
        "matching": {
            "minimum_ocr_confidence": 0.35,
            "fuzzy_threshold": 90,
        },
    }


def load_friends_config(path: Path) -> tuple[dict[str, Any], list[KnownUser]]:
    import json

    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"Cannot parse friend config {path}: {error}") from error
    if not isinstance(config, dict):
        raise ValueError(f"Friend config {path} must contain a JSON object.")
    if config.get("schema_version") != 1:
        raise ValueError("Friend config schema_version must be 1.")

    raw_users = config.get("users")
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
        "minimum_ocr_confidence": matching.get("minimum_ocr_confidence", 0.35),
        "fuzzy_threshold": matching.get("fuzzy_threshold", 90),
    }
    return json_hash(value)


def _config_number(config: dict[str, Any], name: str, default: float) -> float:
    matching = config.get("matching") if isinstance(config.get("matching"), dict) else {}
    value = matching.get(name, default)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"matching.{name} must be a number.")
    return float(value)


def _candidate_strings(text: str) -> set[str]:
    tokens = [normalize(token) for token in re.findall(r"[^\W_]+", text, flags=re.UNICODE)]
    tokens = [token for token in tokens if token]
    candidates = {normalize(text)} if normalize(text) else set()
    for index in range(len(tokens)):
        for width in range(1, min(4, len(tokens) - index) + 1):
            candidates.add("".join(tokens[index : index + width]))
    return candidates


def _best_match_score(observed_text: str, target: str) -> float:
    normalized_target = normalize(target)
    if not normalized_target:
        return 0.0
    scores: list[float] = []
    for candidate in _candidate_strings(observed_text):
        if candidate == normalized_target:
            return 1.0
        if len(normalized_target) <= 4:
            continue
        scores.append(ratio(candidate, normalized_target) / 100.0)
    return max(scores, default=0.0)


def resolve_observations(
    observations: list[dict[str, Any]], config: dict[str, Any], users: list[KnownUser]
) -> dict[str, Any]:
    minimum_ocr_confidence = _config_number(config, "minimum_ocr_confidence", 0.35)
    fuzzy_threshold = _config_number(config, "fuzzy_threshold", 90) / 100.0
    if not 0 <= minimum_ocr_confidence <= 1:
        raise ValueError("matching.minimum_ocr_confidence must be from 0 through 1.")
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
        if confidence < minimum_ocr_confidence or normalize(text) in ignored:
            continue

        for user in users:
            aliases = (user.name, *user.aliases)
            best_alias = max(aliases, key=lambda alias: _best_match_score(text, alias))
            score = _best_match_score(text, best_alias)
            if score < fuzzy_threshold:
                continue

            evidence = {
                "matched_text": text,
                "alias": best_alias,
                "ocr_confidence": round(float(confidence), 4),
                "match_score": round(score, 4),
                "confidence": round(float(confidence) * score, 4),
                "seen_at_s": observation.get("seen_at_s"),
                "bounding_box": observation.get("bounding_box"),
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
