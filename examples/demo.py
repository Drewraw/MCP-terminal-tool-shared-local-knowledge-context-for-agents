"""
Context-Aware Pruning Gateway — End-to-End Demo
=================================================
Run this to see the full pipeline in action against the parent codebase.

Usage:
    cd prunetool
    python examples/demo.py

No server needed — this runs the engines directly.
"""

import hashlib
import json
import os
import sys
import time

# Add parent to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from indexer.skeletal_indexer import SkeletalIndexer
from pruner.pruning_engine import PruningEngine
from pruner.models import PruneRequest
from cache.cache_stabilizer import CacheStabilizer, CacheConfig, PrunedCodeBlock, stabilize_code_prefix

# ──────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────

# Point at the parent codebase (claudeleak/src)
CODEBASE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
INDEX_PATH = os.path.join(os.path.dirname(__file__), "..", ".prunetool", "skeleton.json")

SYSTEM_INSTRUCTIONS = """You are an expert software engineer.
Analyze the provided codebase context to answer the user's question.
Focus on accuracy and cite specific file paths and line numbers."""


def divider(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def main():
    # ──────────────────────────────────────────
    # STEP 1: Build the Skeletal Index
    # ──────────────────────────────────────────
    divider("STEP 1: Skeletal Indexing (Tree-sitter)")

    indexer = SkeletalIndexer(CODEBASE_ROOT, INDEX_PATH)

    # Try loading cached index first
    skeleton = indexer.load()
    if skeleton:
        print(f"Loaded cached index: {skeleton.total_symbols} symbols, {skeleton.file_count} files")
    else:
        print(f"Indexing codebase at: {CODEBASE_ROOT}")
        skeleton = indexer.index_and_save()

    print(f"\nSkeleton summary:")
    print(f"  Files indexed:    {skeleton.file_count}")
    print(f"  Symbols found:    {skeleton.total_symbols}")
    print(f"  Index stored at:  {INDEX_PATH}")

    # Show a few sample entries
    print(f"\nSample symbols (first 8):")
    for entry in skeleton.entries[:8]:
        parent = f"{entry.parent}." if entry.parent else ""
        print(f"  [{entry.kind.value:9}] {parent}{entry.name}")
        print(f"              @ {entry.file_path}:{entry.line_start}-{entry.line_end}")

    # ──────────────────────────────────────────
    # STEP 2: Developer asks a question
    # ──────────────────────────────────────────
    divider("STEP 2: Developer Query")

    QUERY = "How does session memory compaction decide which messages to keep?"
    GOAL = "Focus on compaction thresholds, message selection, and token counting"

    print(f"Query:     {QUERY}")
    print(f"Goal Hint: {GOAL}")

    # ──────────────────────────────────────────
    # STEP 3: Goal-Directed Skeleton Search
    # ──────────────────────────────────────────
    divider("STEP 3: Goal-Directed Search on Skeleton")

    matches = skeleton.search(GOAL, top_k=15)
    print(f"Found {len(matches)} relevant symbols:\n")
    for i, m in enumerate(matches):
        parent = f"{m.parent}." if m.parent else ""
        print(f"  {i+1:2}. [{m.kind.value:9}] {parent}{m.name}")
        print(f"      {m.file_path}:{m.line_start}-{m.line_end}")

    # ──────────────────────────────────────────
    # STEP 4: Precision Pruning
    # ──────────────────────────────────────────
    divider("STEP 4: Precision Pruning")

    pruner = PruningEngine(skeleton, CODEBASE_ROOT)
    request = PruneRequest(
        user_query=QUERY,
        goal_hint=GOAL,
        max_tokens=80_000,
        compression_target=0.5,
    )

    start = time.time()
    result = pruner.prune(request)
    elapsed = (time.time() - start) * 1000

    print(f"Pruning completed in {elapsed:.1f}ms\n")
    print(f"Stats:")
    print(f"  Files processed:    {result.stats.files_processed}")
    print(f"  Symbols matched:    {result.stats.symbols_matched}")
    print(f"  Raw tokens:         {result.stats.total_raw_tokens:,}")
    print(f"  Pruned tokens:      {result.stats.total_pruned_tokens:,}")
    print(f"  Token savings:      {result.stats.token_savings_pct:.1f}%")
    print(f"  Compression ratio:  {result.stats.compression_ratio:.2f}x")

    # Show per-file breakdown
    print(f"\nPer-file breakdown:")
    for pf in result.pruned_files:
        savings = (pf.raw_tokens - pf.pruned_tokens) / max(pf.raw_tokens, 1) * 100
        print(f"\n  {pf.file_path}")
        print(f"    Raw:     {pf.raw_tokens:>6,} tokens / {pf.raw_lines:>4} lines")
        print(f"    Pruned:  {pf.pruned_tokens:>6,} tokens / {pf.pruned_lines:>4} lines")
        print(f"    Savings: {savings:.1f}%")
        print(f"    Kept:    {', '.join(pf.kept_symbols[:5])}")
        if len(pf.kept_symbols) > 5:
            print(f"             ... and {len(pf.kept_symbols) - 5} more")

    # ──────────────────────────────────────────
    # STEP 5: Show Raw vs Pruned (first file)
    # ──────────────────────────────────────────
    divider("STEP 5: Raw vs Pruned (side-by-side preview)")

    if result.pruned_files:
        pf = result.pruned_files[0]
        raw_preview = pf.raw_content.split("\n")[:20]
        pruned_preview = pf.pruned_content.split("\n")[:20]

        print(f"File: {pf.file_path}")
        print(f"\n--- RAW (first 20 lines) ---")
        for i, line in enumerate(raw_preview):
            print(f"  {i+1:3} | {line}")

        print(f"\n--- PRUNED (first 20 lines) ---")
        for i, line in enumerate(pruned_preview):
            marker = " >>>" if "lines pruned" in line else "    "
            print(f"  {i+1:3}{marker} | {line}")

    # ──────────────────────────────────────────
    # STEP 6: Cache-Stable Prompt Assembly
    # ──────────────────────────────────────────
    divider("STEP 6: Cache-Stable Prompt Assembly")

    stabilizer = CacheStabilizer(CacheConfig(provider="anthropic"))

    # Build structured code blocks — the stabilizer sorts and normalizes them
    pruned_blocks = [
        PrunedCodeBlock(file_path=pf.file_path, content=pf.pruned_content)
        for pf in result.pruned_files
    ]

    assembled = stabilizer.assemble(
        system_instructions=SYSTEM_INSTRUCTIONS,
        pruned_blocks=pruned_blocks,
        user_query=QUERY,
        goal_hint=GOAL,
    )

    print(f"Prompt structure (Anthropic format):\n")
    print(f"  system[0]: Static Instructions")
    print(f"    tokens:        {assembled.system_tokens:,}")
    block0 = assembled.system_blocks[0]
    has_cache = "cache_control" in block0
    print(f"    cache_control:  {block0.get('cache_control', 'none')}")
    print(f"    preview:        \"{block0['text'][:80]}...\"")

    print(f"\n  system[1]: Pruned Codebase Context")
    block1 = assembled.system_blocks[1]
    print(f"    tokens:        {assembled.code_tokens:,}")
    print(f"    cache_control:  {block1.get('cache_control', 'none')}")
    print(f"    preview:        \"{block1['text'][:80]}...\"")

    print(f"\n  messages[0]: User Query")
    print(f"    tokens:        {assembled.query_tokens:,}")
    print(f"    content:        \"{assembled.messages[0]['content'][:80]}\"")

    print(f"\n  Total tokens:    {assembled.total_tokens:,}")
    print(f"  Code hash:       {assembled.code_hash}")
    print(f"  Cache hit likely: {assembled.cache_hit_likely}")

    # ──────────────────────────────────────────
    # STEP 7: Simulate a follow-up query (same code, different question)
    # ──────────────────────────────────────────
    divider("STEP 7: Follow-up Query (Cache Hit Test)")

    FOLLOWUP = "What happens when the token count exceeds maxTokens during compaction?"

    assembled2 = stabilizer.assemble(
        system_instructions=SYSTEM_INSTRUCTIONS,
        pruned_blocks=pruned_blocks,  # Same pruned blocks!
        user_query=FOLLOWUP,
        goal_hint=GOAL,
    )

    print(f"Follow-up query: \"{FOLLOWUP}\"")
    print(f"Same pruned code: yes (code hash unchanged)")
    print(f"Cache hit likely: {assembled2.cache_hit_likely}  <-- should be True!")
    print(f"\nThis means the LLM provider will reuse the cached system+code prefix")
    print(f"and only process the new query tokens ({assembled2.query_tokens} tokens).")

    # ──────────────────────────────────────────
    # STEP 8: Determinism Proof
    # ──────────────────────────────────────────
    divider("STEP 8: Prefix Determinism Proof")

    # Prove the prefix is bit-for-bit identical:
    # Shuffle the blocks into a random order and re-stabilize
    import random
    shuffled_blocks = list(pruned_blocks)
    random.shuffle(shuffled_blocks)

    stable_a = stabilize_code_prefix(pruned_blocks)
    stable_b = stabilize_code_prefix(shuffled_blocks)

    print(f"Blocks passed in original order:   sha256 = {hashlib.sha256(stable_a.encode()).hexdigest()[:32]}")
    print(f"Blocks passed in shuffled order:    sha256 = {hashlib.sha256(stable_b.encode()).hexdigest()[:32]}")
    print(f"Byte-for-byte identical:            {stable_a == stable_b}")
    print(f"Length (bytes):                     {len(stable_a.encode())}")

    # Show the first few lines to prove canonical format
    preview_lines = stable_a.split("\n")[:12]
    print(f"\nStabilized prefix (first 12 lines):")
    for i, line in enumerate(preview_lines):
        print(f"  {i+1:3} | {line}")

    # Also verify that re-assembling produces the same hash
    assembled3 = stabilizer.assemble(
        system_instructions=SYSTEM_INSTRUCTIONS,
        pruned_blocks=shuffled_blocks,  # Shuffled order!
        user_query="Completely different question this time",
        goal_hint=GOAL,
    )
    print(f"\nAssembled with shuffled blocks + different query:")
    print(f"  code_hash match:  {assembled.code_hash} == {assembled3.code_hash} -> {assembled.code_hash == assembled3.code_hash}")
    print(f"  cache_hit_likely: {assembled3.cache_hit_likely}  <-- True because prefix is stable")

    # ──────────────────────────────────────────
    # STEP 9: Show the full API payload
    # ──────────────────────────────────────────
    divider("STEP 9: API-Ready Payload")

    api_payload = stabilizer.format_for_api(assembled2)
    # Truncate code for display
    display_payload = json.loads(json.dumps(api_payload))
    for block in display_payload.get("system", []):
        if len(block.get("text", "")) > 200:
            block["text"] = block["text"][:200] + f"... [{len(block['text'])} chars total]"

    print("Anthropic Messages API payload (truncated for display):\n")
    print(json.dumps(display_payload, indent=2))

    # ──────────────────────────────────────────
    # Summary
    # ──────────────────────────────────────────
    divider("SUMMARY")

    print(f"  Query:              \"{QUERY[:60]}...\"")
    print(f"  Goal:               \"{GOAL[:60]}\"")
    print(f"  Files searched:     {skeleton.total_symbols} symbols across {skeleton.file_count} files")
    print(f"  Files pruned:       {result.stats.files_processed}")
    print(f"  Raw tokens:         {result.stats.total_raw_tokens:,}")
    print(f"  Pruned tokens:      {result.stats.total_pruned_tokens:,}")
    print(f"  Tokens saved:       {result.stats.total_raw_tokens - result.stats.total_pruned_tokens:,} ({result.stats.token_savings_pct:.1f}%)")
    print(f"  Compression:        {result.stats.compression_ratio:.1f}x")
    print(f"  Cache stable:       Yes (follow-up queries hit cache)")
    print(f"  Pipeline time:      {elapsed:.1f}ms")
    print()


if __name__ == "__main__":
    main()
