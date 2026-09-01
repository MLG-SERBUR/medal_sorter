#!/usr/bin/env python3
"""Benchmark the resolution refresh performance: old vs new algorithm."""

import time
import re
from typing import Any
from pathlib import Path
from rapidfuzz.fuzz import ratio as fuzz_ratio
from rapidfuzz.distance import Levenshtein

# Add src to path
import sys
sys.path.insert(0, str(Path(__file__).parent / "src"))

from medal_sorter.matching import KnownUser, resolve_observations, normalize


def _candidate_strings_old(text: str) -> set[str]:
    """Old implementation - generates all subsequences upfront."""
    tokens = [normalize(token) for token in re.findall(r"[^\W_]+", text, flags=re.UNICODE)]
    tokens = [token for token in tokens if token]
    candidates = {normalize(text)} if normalize(text) else set()
    for index in range(len(tokens)):
        for width in range(1, min(4, len(tokens) - index) + 1):
            candidates.add("".join(tokens[index : index + width]))
    return candidates


def _best_match_score_old(observed_text: str, target: str) -> float:
    """Old implementation - calls fuzz_ratio, makes 2+ function calls per user."""
    normalized_target = normalize(target)
    if not normalized_target:
        return 0.0
    scores: list[float] = []
    for candidate in _candidate_strings_old(observed_text):
        if candidate == normalized_target:
            return 1.0
        if len(normalized_target) <= 4:
            continue
        scores.append(fuzz_ratio(candidate, normalized_target) / 100.0)
    return max(scores, default=0.0)


def resolve_observations_old(
    observations: list[dict[str, Any]], config: dict[str, Any], users: list[KnownUser]
) -> dict[str, Any]:
    """Old implementation - inefficient inner loop."""
    fuzzy_threshold = config.get("matching", {}).get("fuzzy_threshold", 90) / 100.0
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
            # OLD: Two separate calls to _best_match_score per user
            best_alias = max(aliases, key=lambda alias: _best_match_score_old(text, alias))
            score = _best_match_score_old(text, best_alias)
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

    usernames = sorted(
        resolved.values(), key=lambda user: (-user["best_confidence"], user["name"].casefold())
    )
    for username in usernames:
        username["seen_at_s"].sort()
        username["best_confidence"] = round(username["best_confidence"], 4)

    return {
        "state": "complete",
        "revision": "test",
        "completed_at": "2026-01-01T00:00:00Z",
        "usernames": usernames,
    }


def generate_test_data(num_observations: int, num_users: int) -> tuple[list[dict[str, Any]], list[KnownUser]]:
    """Generate realistic test data."""
    # Realistic OCR text samples (game UI, usernames, etc)
    ocr_texts = [
        "AlexDiscord",
        "A1ex",
        "AlexOnSteam",
        "BigTank",
        "Healer123",
        "DPS_Zero",
        "Play",
        "Settings",
        "Headshot",
        "Damage: 1250",
        "Score",
        "RANKED",
        "Victory",
        "Defeat",
        "alex discord",
        "ALEXDISCORD",
        "alex",
        "big tank",
        "BIGTANK",
        "healer 123",
        "healer123",
        "dps zero",
        "dps_zero",
        "Unknown User 42",
        "Player #1",
    ]

    observations = []
    for i in range(num_observations):
        observations.append(
            {
                "text": ocr_texts[i % len(ocr_texts)],
                "confidence": 0.75 + (i % 25) * 0.01,
                "seen_at_s": float(i * 4),
            }
        )

    users = [
        KnownUser(name="AlexDiscord", group="friday_friends", aliases=("A1ex", "AlexOnSteam")),
        KnownUser(name="BigTank", group="raid_team", aliases=()),
        KnownUser(name="Healer123", group="raid_team", aliases=()),
        KnownUser(name="DPS_Zero", group="raid_team", aliases=()),
    ] * (num_users // 4 + 1)
    users = users[:num_users]

    return observations, users


def benchmark():
    """Run performance benchmarks."""
    config = {
        "matching": {"fuzzy_threshold": 90},
        "ignored_ocr_text": ["Play", "Settings", "Headshot"],
    }

    test_cases = [
        ("Small (50 obs, 4 users)", 50, 4),
        ("Medium (200 obs, 12 users)", 200, 12),
        ("Large (500 obs, 25 users)", 500, 25),
        ("Very Large (1000 obs, 50 users)", 1000, 50),
    ]

    print("=" * 80)
    print("RESOLUTION REFRESH PERFORMANCE BENCHMARK")
    print("=" * 80)
    print()

    for label, num_obs, num_users in test_cases:
        observations, users = generate_test_data(num_obs, num_users)

        # Benchmark old implementation
        start = time.perf_counter()
        result_old = resolve_observations_old(observations, config, users)
        old_time = time.perf_counter() - start

        # Benchmark new implementation
        start = time.perf_counter()
        result_new = resolve_observations(observations, config, users)
        new_time = time.perf_counter() - start

        speedup = old_time / new_time if new_time > 0 else float("inf")
        improvement = (1 - new_time / old_time) * 100 if old_time > 0 else 0

        print(f"{label}:")
        print(f"  Old algorithm: {old_time*1000:.2f} ms")
        print(f"  New algorithm: {new_time*1000:.2f} ms")
        print(f"  Speedup: {speedup:.2f}x ({improvement:.1f}% faster)")
        print(f"  Matched usernames: {len(result_new['usernames'])}")
        print()


if __name__ == "__main__":
    benchmark()
