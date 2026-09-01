#!/usr/bin/env python3
"""Detailed analysis of resolution refresh optimizations."""

import time
from pathlib import Path
from typing import Any

# Add src to path
import sys
sys.path.insert(0, str(Path(__file__).parent / "src"))

from medal_sorter.matching import KnownUser, normalize, _best_match_score
from benchmark_resolution import (
    _best_match_score_old,
    _candidate_strings_old,
    generate_test_data,
)


def analyze_call_patterns():
    """Analyze how many fuzzy match calls are made."""
    config = {
        "matching": {"fuzzy_threshold": 90},
        "ignored_ocr_text": ["Play", "Settings"],
    }
    
    observations, users = generate_test_data(num_observations=100, num_users=8)
    
    # Count old algorithm calls
    print("OLD ALGORITHM - Call Pattern Analysis")
    print("=" * 60)
    
    old_score_calls = 0
    old_candidate_calls = 0
    
    for observation in observations[:10]:  # Sample 10 observations
        text = observation.get("text")
        if not text:
            continue
        
        for user in users:
            aliases = (user.name, *user.aliases)
            
            # Each alias needs _candidate_strings call
            for alias in aliases:
                old_candidate_calls += 1
            
            # Find best: calls _best_match_score for each alias
            old_score_calls += len(aliases)
            
            # Then calls it again to get the score
            old_score_calls += 1
    
    print(f"Sample observations analyzed: 10")
    print(f"Users analyzed per observation: {len(users)}")
    print(f"Total _candidate_strings() calls: {old_candidate_calls}")
    print(f"Total _best_match_score() calls: {old_score_calls}")
    print(f"  (This translates to ~{old_candidate_calls} fuzzy scoring operations)")
    print()
    
    print("NEW ALGORITHM - Call Pattern Analysis")
    print("=" * 60)
    print(f"Total _candidate_strings() calls: 0 (inlined)")
    print(f"Total _best_match_score() calls: {len(aliases)} per user + 0 duplicates")
    print(f"  (Same number of aliases checked, but no duplicate calls)")
    print(f"  Reduction: ~{100 * 1 / (1 + 1 / len(aliases)):.0f}% fewer calls per user")
    print()


def benchmark_components():
    """Benchmark individual components."""
    print("COMPONENT-LEVEL BENCHMARKING")
    print("=" * 60)
    print()
    
    test_cases = [
        ("Short text, short name (≤4 chars)", "lex", "Lex", 10000),
        ("Medium text, long name", "AlexDiscord", "AlexDiscord", 10000),
        ("Long text, long name", "PLAYER ALEXDISCORD IN GAME MODE RANKED", "AlexDiscord", 10000),
        ("Typo: text vs name", "AleXDiskord", "AlexDiscord", 10000),
    ]
    
    for label, text, name, iterations in test_cases:
        # Old approach
        start = time.perf_counter()
        for _ in range(iterations):
            _best_match_score_old(text, name)
        old_time = time.perf_counter() - start
        
        # New approach
        start = time.perf_counter()
        for _ in range(iterations):
            _best_match_score(text, name)
        new_time = time.perf_counter() - start
        
        speedup = old_time / new_time if new_time > 0 else float("inf")
        print(f"{label}:")
        print(f"  Text: '{text}' vs Name: '{name}'")
        print(f"  Old: {old_time*1000:.2f} ms ({old_time/iterations*1e6:.2f} µs per call)")
        print(f"  New: {new_time*1000:.2f} ms ({new_time/iterations*1e6:.2f} µs per call)")
        print(f"  Speedup: {speedup:.2f}x")
        print()


if __name__ == "__main__":
    analyze_call_patterns()
    benchmark_components()
