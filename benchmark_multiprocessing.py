#!/usr/bin/env python3
"""Benchmark threading performance for resolution refresh."""

import time
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

# Add src to path
import sys
sys.path.insert(0, str(Path(__file__).parent / "src"))

from medal_sorter.matching import KnownUser, resolve_observations
from medal_sorter.cli import _resolve_observations_parallel, _record_resolution


def generate_large_test_data(num_records: int) -> tuple[list[tuple[str, dict[str, Any]]], dict[str, Any], list[KnownUser]]:
    """Generate realistic large test data for benchmarking."""
    
    ocr_texts = [
        "AlexDiscord", "A1ex", "AlexOnSteam", "BigTank", "Healer123", "DPS_Zero",
        "Play", "Settings", "Headshot", "Damage: 1250", "Score", "RANKED",
        "Victory", "Defeat", "alex discord", "ALEXDISCORD", "alex", "big tank",
        "BIGTANK", "healer 123", "healer123", "dps zero", "dps_zero",
        "Unknown User 42", "Player #1", "Observer Mode", "Match Start",
        "First Blood", "Kill Streak", "Team: Red", "Team: Blue", "Respawn in 3",
    ]
    
    config = {
        "schema_version": 1,
        "matching": {"fuzzy_threshold": 90},
        "ignored_ocr_text": ["Play", "Settings", "Victory", "Defeat"],
    }
    
    users = [
        KnownUser(name="AlexDiscord", group="friday_friends", aliases=("A1ex", "AlexOnSteam")),
        KnownUser(name="BigTank", group="raid_team", aliases=()),
        KnownUser(name="Healer123", group="raid_team", aliases=()),
        KnownUser(name="DPS_Zero", group="raid_team", aliases=()),
    ]
    
    # Generate records with realistic OCR observations
    records: list[tuple[str, dict[str, Any]]] = []
    for i in range(num_records):
        # Each record has 8-12 observations
        num_obs = 8 + (i % 5)
        observations = []
        for j in range(num_obs):
            observations.append({
                "text": ocr_texts[(i * num_obs + j) % len(ocr_texts)],
                "confidence": 0.75 + (j % 25) * 0.01,
                "seen_at_s": float(j * 4),
            })
        
        record: dict[str, Any] = {
            "path": f"Marvel Rivals/clip_{i:04d}.mp4",
            "file": f"W:\\medal\\Clips\\Marvel Rivals\\clip_{i:04d}.mp4",
            "analysis": {
                "usernames": {
                    "ocr": {
                        "state": "complete",
                        "pipeline_revision": "test",
                        "observations": observations,
                        "completed_at": "2026-01-01T00:00:00Z",
                    }
                }
            }
        }
        records.append((f"key_{i}", record))
    
    return records, config, users


def benchmark_parallel_resolution():
    """Benchmark resolution refresh with different worker counts."""
    
    print("=" * 80)
    print("THREADED RESOLUTION REFRESH BENCHMARK")
    print("=" * 80)
    print()
    
    test_cases = [
        ("Small (10 records)", 10),
        ("Medium (50 records)", 50),
        ("Large (100 records)", 100),
        ("Very Large (200 records)", 200),
        ("Huge (500 records)", 500),
    ]
    
    cpu_count = os.cpu_count() or 1
    print(f"System CPU count: {cpu_count}")
    print()
    
    for label, num_records in test_cases:
        records, config, users = generate_large_test_data(num_records)
        
        print(f"{label}:")
        print(f"  Records to process: {num_records}")
        total_obs = sum(len(r['analysis']['usernames']['ocr']['observations']) for _, r in records)
        print(f"  Total observations: {total_obs}")
        print()
        
        # Test single-worker (baseline)
        start = time.perf_counter()
        result_single = _resolve_observations_parallel(records, config, users, num_workers=1)
        single_time = time.perf_counter() - start
        
        # Test with optimal worker count (min of 8 and cpu_count)
        optimal_workers = min(8, cpu_count)
        start = time.perf_counter()
        result_multi = _resolve_observations_parallel(records, config, users, num_workers=optimal_workers)
        multi_time = time.perf_counter() - start
        
        # Test with auto-detection (None)
        start = time.perf_counter()
        result_auto = _resolve_observations_parallel(records, config, users, num_workers=None)
        auto_time = time.perf_counter() - start
        
        speedup = single_time / multi_time if multi_time > 0 else float("inf")
        efficiency = (speedup / optimal_workers) * 100 if optimal_workers > 0 else 0
        
        print(f"  Single thread:       {single_time*1000:.2f} ms")
        print(f"  Auto ({optimal_workers} threads):  {auto_time*1000:.2f} ms")
        print(f"  Speedup:             {speedup:.2f}x ({efficiency:.1f}% efficiency)")
        print(f"  Time per record:     {multi_time*1000/num_records:.2f} ms")
        print()


if __name__ == "__main__":
    benchmark_parallel_resolution()
