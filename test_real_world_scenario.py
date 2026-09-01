#!/usr/bin/env python3
"""Realistic end-to-end performance test simulating actual clip analysis."""

import time
import json
from pathlib import Path
from typing import Any

# Add src to path
import sys
sys.path.insert(0, str(Path(__file__).parent / "src"))

from medal_sorter.matching import (
    KnownUser,
    resolve_observations,
)


def simulate_real_clip_analysis():
    """Simulate actual OCR output from multiple game clips."""
    
    # Realistic OCR observations from actual game clips
    # Typical structure: HUD text, usernames, scoreboards, chat
    clip_data = {
        "Marvel_Rivals_Clip_1": [
            {"text": "AlexDiscord", "confidence": 0.92, "seen_at_s": 2.0},
            {"text": "Play", "confidence": 0.88, "seen_at_s": 4.0},
            {"text": "Score: 1250", "confidence": 0.85, "seen_at_s": 6.0},
            {"text": "BigTank", "confidence": 0.89, "seen_at_s": 8.0},
            {"text": "Healer123", "confidence": 0.91, "seen_at_s": 10.0},
            {"text": "A1ex", "confidence": 0.87, "seen_at_s": 12.0},
            {"text": "Settings", "confidence": 0.83, "seen_at_s": 14.0},
            {"text": "DPS_Zero", "confidence": 0.90, "seen_at_s": 16.0},
        ],
        "Marvel_Rivals_Clip_2": [
            {"text": "alexdiscord", "confidence": 0.78, "seen_at_s": 1.0},
            {"text": "BIGTANK", "confidence": 0.84, "seen_at_s": 3.0},
            {"text": "Ranked Match", "confidence": 0.91, "seen_at_s": 5.0},
            {"text": "healer 123", "confidence": 0.82, "seen_at_s": 7.0},
            {"text": "dps zero", "confidence": 0.85, "seen_at_s": 9.0},
            {"text": "Victory", "confidence": 0.93, "seen_at_s": 11.0},
        ],
        "Marvel_Rivals_Clip_3": [
            {"text": "Player AlexOnSteam", "confidence": 0.79, "seen_at_s": 2.5},
            {"text": "Tank Role", "confidence": 0.87, "seen_at_s": 4.5},
            {"text": "Big Tank", "confidence": 0.81, "seen_at_s": 6.5},
            {"text": "Healer123", "confidence": 0.88, "seen_at_s": 8.5},
            {"text": "DPS Zero", "confidence": 0.86, "seen_at_s": 10.5},
            {"text": "Objective", "confidence": 0.90, "seen_at_s": 12.5},
        ],
    }
    
    # Realistic friend config
    config = {
        "schema_version": 1,
        "matching": {"fuzzy_threshold": 90},
        "ignored_ocr_text": ["Play", "Settings", "Victory", "Defeat", "Ranked", "Objective"],
    }
    
    users = [
        KnownUser(name="AlexDiscord", group="friday_friends", aliases=("A1ex", "AlexOnSteam")),
        KnownUser(name="BigTank", group="raid_team", aliases=()),
        KnownUser(name="Healer123", group="raid_team", aliases=()),
        KnownUser(name="DPS_Zero", group="raid_team", aliases=()),
    ]
    
    print("=" * 80)
    print("REAL-WORLD CLIP ANALYSIS SIMULATION")
    print("=" * 80)
    print(f"Analyzing {len(clip_data)} clips with {len(users)} known users")
    print()
    
    total_time = 0
    total_observations = 0
    
    for clip_name, observations in clip_data.items():
        start = time.perf_counter()
        result = resolve_observations(observations, config, users)
        elapsed = time.perf_counter() - start
        total_time += elapsed
        total_observations += len(observations)
        
        matched_users = result.get("usernames", [])
        print(f"{clip_name}:")
        print(f"  Observations: {len(observations)}")
        print(f"  Time: {elapsed*1000:.2f} ms ({elapsed*1e6/len(observations):.2f} µs per obs)")
        print(f"  Matched users: {len(matched_users)}")
        for user in matched_users:
            print(f"    - {user['name']} (group: {user['group']}): {user['occurrences']} matches")
        print()
    
    avg_time_per_obs = total_time * 1e6 / total_observations if total_observations else 0
    print("=" * 80)
    print(f"Total time for {total_observations} observations: {total_time*1000:.2f} ms")
    print(f"Average per observation: {avg_time_per_obs:.2f} µs")
    print(f"Estimated time for 100 clips (~800 obs): {total_time * 100/len(clip_data) * 1000:.2f} ms")
    print()


if __name__ == "__main__":
    simulate_real_clip_analysis()
