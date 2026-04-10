"""Data models for the pruning engine."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PruneRequest:
    """Incoming request to prune context for an LLM query."""
    user_query: str
    file_paths: list[str] = field(default_factory=list)
    goal_hint: Optional[str] = None
    max_tokens: int = 80_000
    compression_target: float = 0.5  # Target 50% reduction


@dataclass
class PrunedFile:
    """A single file after pruning."""
    file_path: str
    raw_content: str
    pruned_content: str
    raw_lines: int
    pruned_lines: int
    raw_tokens: int
    pruned_tokens: int
    kept_symbols: list[str] = field(default_factory=list)
    removed_sections: list[str] = field(default_factory=list)


@dataclass
class PruneStats:
    """Statistics about a pruning operation."""
    total_raw_tokens: int = 0
    total_pruned_tokens: int = 0
    total_raw_lines: int = 0
    total_pruned_lines: int = 0
    files_processed: int = 0
    symbols_matched: int = 0
    compression_ratio: float = 0.0
    token_savings_pct: float = 0.0


@dataclass
class PruneResult:
    """Complete result of a pruning operation."""
    pruned_files: list[PrunedFile] = field(default_factory=list)
    stats: PruneStats = field(default_factory=PruneStats)
    goal_hint_used: str = ""
    assembled_prompt: str = ""
