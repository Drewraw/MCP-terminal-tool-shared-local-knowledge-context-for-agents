"""
Agent 2 vs Agent 3 Token Comparison (No API Key Required)
==========================================================
Counts tokens using the prunetool's built-in counter.

Agent 2 — Prunetool-guided:
  - Calls /scout-select to get files
  - Calls /prune to get EXACT token counts for those files

Agent 3 — Raw:
  - You paste the agent's self-reported "files read + lines" here
  - Script estimates tokens from lines

Usage:
  python compare_agents.py "how does followup digest work"
  python compare_agents.py "your query" --gateway http://localhost:8000
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

GATEWAY_URL = "http://localhost:8000"
# Rough token estimate: 4 chars per token (standard approximation)
CHARS_PER_TOKEN = 4
# Average chars per line of code
CHARS_PER_LINE = 45


def scout_select(query: str, gateway: str) -> list[str]:
    payload = json.dumps({"user_query": query}).encode()
    req = urllib.request.Request(
        f"{gateway}/scout-select",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read())
    except urllib.error.URLError as e:
        print(f"[!] Cannot reach gateway at {gateway}: {e}")
        sys.exit(1)

    files = []
    for folder_data in data.get("selected_folders", {}).values():
        files.extend(folder_data.get("files", []))
    return sorted(set(files))


def prune_and_count(query: str, file_paths: list[str], gateway: str) -> dict:
    """Call /prune to get exact token counts for selected files."""
    payload = json.dumps({
        "user_query": query,
        "file_paths": file_paths,
        "max_tokens": 200000,
    }).encode()
    req = urllib.request.Request(
        f"{gateway}/prune",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"[!] /prune error: {e}")
        return {}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("query", help="The developer query to test")
    parser.add_argument("--gateway", default=GATEWAY_URL)
    parser.add_argument(
        "--agent3-lines", type=int, default=None,
        help="Lines Agent 3 reported reading (for token estimate)"
    )
    args = parser.parse_args()

    query   = args.query
    gateway = args.gateway.rstrip("/")

    print(f"\n{'='*60}")
    print(f"Query : {query}")
    print(f"{'='*60}\n")

    # ── Agent 2: Prunetool-guided ────────────────────────────────────
    print("[ Agent 2 ] Prunetool-guided")
    print("  Calling /scout-select...")
    t0 = time.time()
    selected_files = scout_select(query, gateway)
    scout_ms = round((time.time() - t0) * 1000)
    print(f"  Scout selected {len(selected_files)} files in {scout_ms}ms")

    print("  Calling /prune for exact token counts...")
    prune_result = prune_and_count(query, selected_files, gateway)
    stats      = prune_result.get("stats", {})
    cache_info = prune_result.get("cache_info", {})

    a2_raw_tokens    = stats.get("total_raw_tokens", 0)
    a2_pruned_tokens = stats.get("total_pruned_tokens", 0)
    a2_total_input   = cache_info.get("total_tokens", 0)   # system + code + query
    a2_files         = stats.get("files_processed", 0)
    a2_savings_pct   = stats.get("token_savings_pct", 0)

    print(f"  Files processed : {a2_files}")
    print(f"  Raw tokens      : {a2_raw_tokens:,}")
    print(f"  Pruned tokens   : {a2_pruned_tokens:,}  ({a2_savings_pct:.1f}% saved by pruner)")
    print(f"  Total input     : {a2_total_input:,}  (pruned code + system + query)")

    # ── Agent 3: Raw estimate ────────────────────────────────────────
    print("\n[ Agent 3 ] Raw — estimated from lines reported")

    agent3_lines = args.agent3_lines
    if agent3_lines is None:
        try:
            agent3_lines = int(input("  How many lines did Agent 3 report reading? "))
        except (ValueError, EOFError):
            agent3_lines = 3500  # default from last test

    a3_estimated_tokens = round(agent3_lines * CHARS_PER_LINE / CHARS_PER_TOKEN)
    print(f"  Lines read      : {agent3_lines:,}")
    print(f"  Estimated tokens: {a3_estimated_tokens:,}  (~{CHARS_PER_LINE} chars/line ÷ {CHARS_PER_TOKEN} chars/token)")

    # ── Comparison Table ─────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("TOKEN COMPARISON")
    print(f"{'='*60}")
    print(f"{'Metric':<38} {'Agent 2':>10} {'Agent 3':>10}")
    print(f"{'-'*60}")
    print(f"{'Files used':<38} {len(selected_files):>10} {'unknown':>10}")
    print(f"{'Raw file tokens (before pruning)':<38} {a2_raw_tokens:>10,} {'n/a':>10}")
    print(f"{'Pruned code tokens':<38} {a2_pruned_tokens:>10,} {'n/a':>10}")
    print(f"{'Total input tokens (to LLM)':<38} {a2_total_input:>10,} {a3_estimated_tokens:>10,}")
    print(f"{'Scout overhead (ms)':<38} {scout_ms:>10} {'n/a':>10}")
    print(f"{'-'*60}")

    if a3_estimated_tokens > 0:
        saved = a3_estimated_tokens - a2_total_input
        pct   = round(saved / a3_estimated_tokens * 100, 1)
        if saved > 0:
            print(f"  Agent 2 sent {saved:,} FEWER tokens to the LLM ({pct}% savings)")
        else:
            print(f"  Agent 2 sent {-saved:,} MORE tokens (file contents added context)")

    # ── Save log ─────────────────────────────────────────────────────
    log = {
        "query":      query,
        "timestamp":  time.strftime("%Y-%m-%dT%H:%M:%S"),
        "agent2": {
            "files_selected":    selected_files,
            "files_processed":   a2_files,
            "raw_tokens":        a2_raw_tokens,
            "pruned_tokens":     a2_pruned_tokens,
            "total_input_tokens": a2_total_input,
            "scout_ms":          scout_ms,
            "pruner_savings_pct": a2_savings_pct,
        },
        "agent3": {
            "lines_reported":    agent3_lines,
            "estimated_tokens":  a3_estimated_tokens,
        },
    }
    log_path = Path(__file__).parent / "token_log.jsonl"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(log) + "\n")
    print(f"\n  Saved to token_log.jsonl")


if __name__ == "__main__":
    main()
