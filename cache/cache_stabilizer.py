"""
Cache Stabilizer — Prefix-Stable Prompt Assembly
==================================================
Ensures Anthropic/DeepSeek native prompt caching stays active by
enforcing a rigid, deterministic prompt structure:

    [System Instructions]  <-- cache_control: ephemeral
    [Pruned Codebase]      <-- cache_control: ephemeral
    [User Query]           <-- varies per turn

Determinism contract:
  The system[0]+system[1] prefix is **bit-for-bit identical** across
  calls as long as the underlying relevant code hasn't changed.
  This is achieved by:
    1. Sorting all code blocks alphabetically by file path
    2. Stripping non-essential metadata (timestamps, removed_sections,
       kept_symbols, body hashes) from the output
    3. Normalizing whitespace (trailing spaces, trailing newlines)
    4. Hashing AFTER normalization — so the hash itself is stable

Compatible with:
- Anthropic's prompt caching (cache_control: {"type": "ephemeral"})
- DeepSeek's prefix caching (automatic, prefix-stable)
- Any provider that caches based on message prefix stability
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Optional

try:
    from ..pruner.token_counter import count_tokens
except ImportError:
    from pruner.token_counter import count_tokens


@dataclass
class CacheConfig:
    """Configuration for the cache stabilizer."""
    # Cache TTL hint (Anthropic supports "ephemeral" = 5min default)
    cache_type: str = "ephemeral"
    # Maximum tokens for the system block
    max_system_tokens: int = 4_000
    # Maximum tokens for the pruned code block
    max_code_tokens: int = 100_000
    # Whether to add cache_control markers
    enable_cache_markers: bool = True
    # Provider: "anthropic", "deepseek", "openai"
    provider: str = "anthropic"


@dataclass
class AssembledPrompt:
    """The final assembled prompt ready for the LLM API."""
    system_blocks: list[dict[str, Any]]
    messages: list[dict[str, Any]]
    # Metadata
    system_tokens: int = 0
    code_tokens: int = 0
    query_tokens: int = 0
    total_tokens: int = 0
    code_hash: str = ""
    cache_hit_likely: bool = False


@dataclass
class PrunedCodeBlock:
    """A single file's pruned code, ready for stabilization."""
    file_path: str
    content: str


# Regex to normalize trailing whitespace on every line
_TRAILING_WS = re.compile(r"[ \t]+$", re.MULTILINE)


def stabilize_code_prefix(blocks: list[PrunedCodeBlock]) -> str:
    """
    Produce a bit-for-bit deterministic code string from pruned blocks.

    Guarantees:
      - Blocks are sorted alphabetically by file_path (forward slash normalized)
      - Each block uses the canonical format:
            === <filepath> ===
            <code>
      - Trailing whitespace is stripped from every line
      - Exactly one trailing newline at the end of the full string
      - No metadata (kept_symbols, removed_sections, timestamps, hashes)
      - Consecutive blank lines collapsed to one

    The output is identical for identical input regardless of the order
    blocks were produced by the pruner or the dict iteration order.
    """
    if not blocks:
        return ""

    # Sort alphabetically by normalized path (forward slashes, lowercase key)
    sorted_blocks = sorted(blocks, key=lambda b: b.file_path.replace("\\", "/").lower())

    parts: list[str] = []
    for block in sorted_blocks:
        # Normalize the path separator to forward slash
        norm_path = block.file_path.replace("\\", "/")

        # Normalize the code content
        code = _normalize_content(block.content)

        # Canonical format — no markdown fences, no metadata
        parts.append(f"=== {norm_path} ===\n{code}")

    # Join with exactly one blank line between files
    joined = "\n\n".join(parts)

    # Exactly one trailing newline
    return joined.rstrip("\n") + "\n"


def _normalize_content(text: str) -> str:
    """
    Normalize code content for bit-for-bit stability.
      - Strip trailing whitespace from every line
      - Collapse 3+ consecutive blank lines to 2
      - Strip leading/trailing blank lines from the block
    """
    # Strip trailing whitespace per line
    text = _TRAILING_WS.sub("", text)

    # Collapse runs of 3+ blank lines to exactly 2
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")

    # Strip leading/trailing blank lines
    text = text.strip("\n")

    return text


class CacheStabilizer:
    """
    Assembles prompts in a cache-friendly, deterministic structure.

    The prompt is built in three rigid layers:
    1. SYSTEM: Static instructions (changes rarely -> cached)
    2. CODE:   Pruned codebase context (changes per-index -> cached between queries)
    3. QUERY:  User's actual question (changes every turn -> never cached)

    The system[0]+system[1] prefix is bit-for-bit identical across
    calls as long as (a) the system instructions haven't changed and
    (b) the underlying relevant code hasn't changed.
    """

    def __init__(self, config: Optional[CacheConfig] = None):
        self.config = config or CacheConfig()
        self._last_code_hash: Optional[str] = None
        self._last_system_hash: Optional[str] = None

    def assemble(
        self,
        system_instructions: str,
        pruned_blocks: list[PrunedCodeBlock],
        user_query: str,
        goal_hint: str = "",
        extra_context: str = "",
    ) -> AssembledPrompt:
        """
        Assemble the final prompt with a prefix-stable structure.

        Args:
            system_instructions: Static system prompt (rarely changes)
            pruned_blocks: List of PrunedCodeBlock (sorted+normalized internally)
            user_query: The developer's question (varies every turn)
            goal_hint: Optional goal hint (included in code block header)
            extra_context: Optional additional context
        """
        # Stabilize the code prefix — sort, normalize, strip metadata
        stable_code = stabilize_code_prefix(pruned_blocks)

        # Normalize system instructions the same way
        stable_instructions = _normalize_content(system_instructions)

        # Hash AFTER normalization — the hash is over the exact bytes
        # that will appear in the prompt
        code_hash = hashlib.sha256(stable_code.encode("utf-8")).hexdigest()[:16]
        system_hash = hashlib.sha256(stable_instructions.encode("utf-8")).hexdigest()[:16]

        cache_hit_likely = (
            code_hash == self._last_code_hash
            and system_hash == self._last_system_hash
        )

        self._last_code_hash = code_hash
        self._last_system_hash = system_hash

        # Build the deterministic system blocks
        system_blocks = self._build_system_blocks(
            stable_instructions, stable_code, goal_hint, extra_context
        )

        # Build messages (query is NOT part of the cached prefix)
        messages = self._build_messages(user_query)

        # Count tokens
        system_text = " ".join(b.get("text", "") for b in system_blocks)
        system_tokens = count_tokens(system_text)
        code_tokens = count_tokens(stable_code)
        query_tokens = count_tokens(user_query)

        return AssembledPrompt(
            system_blocks=system_blocks,
            messages=messages,
            system_tokens=system_tokens,
            code_tokens=code_tokens,
            query_tokens=query_tokens,
            total_tokens=system_tokens + code_tokens + query_tokens,
            code_hash=code_hash,
            cache_hit_likely=cache_hit_likely,
        )

    def _build_system_blocks(
        self,
        instructions: str,
        stable_code: str,
        goal_hint: str,
        extra_context: str,
    ) -> list[dict[str, Any]]:
        """Build the system prompt blocks with cache control markers."""
        blocks: list[dict[str, Any]] = []

        # Block 1: Static system instructions (most stable -> first cache breakpoint)
        system_block: dict[str, Any] = {
            "type": "text",
            "text": instructions,
        }
        if self.config.enable_cache_markers and self.config.provider == "anthropic":
            system_block["cache_control"] = {"type": self.config.cache_type}
        blocks.append(system_block)

        # Block 2: Pruned codebase context (stable across queries -> second breakpoint)
        # The header is fixed-format — no variable metadata
        code_header = "## Relevant Codebase Context"
        if goal_hint:
            code_header += f"\n### Goal: {goal_hint}"

        code_text = f"{code_header}\n\n{stable_code}"

        if extra_context:
            normalized_extra = _normalize_content(extra_context)
            code_text += f"\n\n## Additional Context\n{normalized_extra}"

        code_block: dict[str, Any] = {
            "type": "text",
            "text": code_text,
        }
        if self.config.enable_cache_markers and self.config.provider == "anthropic":
            code_block["cache_control"] = {"type": self.config.cache_type}
        blocks.append(code_block)

        return blocks

    def _build_messages(self, user_query: str) -> list[dict[str, Any]]:
        """Build the messages array (user query — varies per turn)."""
        return [
            {
                "role": "user",
                "content": user_query.strip(),
            }
        ]

    def format_for_api(self, assembled: AssembledPrompt) -> dict[str, Any]:
        """Format the assembled prompt for the target API."""
        if self.config.provider == "anthropic":
            return self._format_anthropic(assembled)
        elif self.config.provider == "deepseek":
            return self._format_deepseek(assembled)
        else:
            return self._format_openai_compat(assembled)

    def _format_anthropic(self, assembled: AssembledPrompt) -> dict[str, Any]:
        return {
            "system": assembled.system_blocks,
            "messages": assembled.messages,
        }

    def _format_deepseek(self, assembled: AssembledPrompt) -> dict[str, Any]:
        system_text = "\n\n".join(b["text"] for b in assembled.system_blocks)
        return {
            "messages": [
                {"role": "system", "content": system_text},
                *assembled.messages,
            ]
        }

    def _format_openai_compat(self, assembled: AssembledPrompt) -> dict[str, Any]:
        system_text = "\n\n".join(b["text"] for b in assembled.system_blocks)
        return {
            "messages": [
                {"role": "system", "content": system_text},
                *assembled.messages,
            ]
        }

    def get_cache_stats(self) -> dict[str, Any]:
        return {
            "last_code_hash": self._last_code_hash,
            "last_system_hash": self._last_system_hash,
            "provider": self.config.provider,
            "cache_type": self.config.cache_type,
            "markers_enabled": self.config.enable_cache_markers,
        }
