"""
Token counting utilities.
Uses tiktoken (cl100k_base) for accurate counts,
falls back to word-based estimation if unavailable.
"""

from __future__ import annotations

_ENCODER = None
_INIT_ATTEMPTED = False


def _get_encoder():
    global _ENCODER, _INIT_ATTEMPTED
    if _INIT_ATTEMPTED:
        return _ENCODER
    _INIT_ATTEMPTED = True
    try:
        import tiktoken
        _ENCODER = tiktoken.get_encoding("cl100k_base")
    except ImportError:
        _ENCODER = None
    return _ENCODER


def count_tokens(text: str) -> int:
    """Count tokens in a string. Uses tiktoken if available, else estimates."""
    if not text:
        return 0
    enc = _get_encoder()
    if enc:
        return len(enc.encode(text, disallowed_special=()))
    # Rough estimate: ~4 chars per token for code
    return max(1, len(text) // 4)


def estimate_tokens_for_lines(lines: list[str]) -> int:
    """Estimate token count for a list of lines."""
    return count_tokens("\n".join(lines))
