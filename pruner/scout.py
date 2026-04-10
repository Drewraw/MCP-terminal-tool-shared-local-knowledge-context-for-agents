"""
Context-Aware Scout — Llama 3.1-8B-Instant Symbol Ranker
==========================================================
A lightweight LLM that acts as a "neural skimmer" inspired by
SWE-Pruner's self-adaptive context pruning.

Takes:
  - User query + goal hint
  - Multi-layered project context (README + annotations + skeleton)
  - Full symbol list from skeletal index

Returns:
  - Ranked list of relevant symbol identifiers for surgical pruning

Pluggable backend:
  - Ollama (local, offline):  llama3.1:8b on localhost:11434
  - Groq   (cloud, fast):     llama-3.1-8b-instant via API

The Scout does NOT prune code — it only picks which symbols matter.
The PruningEngine then does the actual surgical extraction.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Optional, List


@dataclass
class ScoutResult:
    """Result from the Scout's symbol ranking."""
    ranked_symbols: List[str] = field(default_factory=list)  # Symbol identifiers: "file_path::name"
    reasoning: str = ""             # Brief explanation of why these were chosen
    elapsed_ms: float = 0           # Time taken for the scout call
    backend: str = "none"           # "ollama", "groq", or "fallback"
    model: str = ""                 # Actual model used
    tokens_used: int = 0            # Tokens consumed by the scout


# ── Scout System Prompt ─────────────────────────────────────────────

_SCOUT_SYSTEM = """You are a Code Scout — a fast, precise tool that picks which code symbols are relevant to a developer's query.

You receive:
1. PROJECT CONTEXT: README overview + developer annotations + directory structure
2. SYMBOL LIST: Functions, classes, and enums — some with extra hints:
   - "| values: x|y|z"   → enum member names (use these to match domain terms in the query)
   - "| purpose: ..."     → auto-generated 1-sentence file description
   - "| ..."              → inline doc comment
3. DEVELOPER QUERY: What the developer wants to understand or fix

Your job: Return ONLY a JSON object with the most relevant symbols.

Rules:
- Pick 5-15 symbols that are most relevant to the query
- Use "purpose:" hints — they are the most reliable signal for what a file does
- For "broken", "error", "debug", "not working" queries: look for the DATA PIPELINE files (backend processors, cloud functions, schedulers) not just the UI screens that display the result
- For feature queries: include both the backend logic AND the UI entry point
- Use "values:" hints to identify enums that define domain types named in the query
- Do NOT pick generic utilities (auth_service, theme_service) unless the query is specifically about them
- DO NOT explain. Just return JSON.

Output format (strict JSON, no markdown):
{"symbols": ["file_path::symbol_name", ...], "reason": "one line why"}"""


_SCOUT_USER_TEMPLATE = """## Project Context
{context}

## All Symbols ({symbol_count} total)
{symbols}

## Developer Query
{query}

Return JSON with 5-15 symbols relevant to the query. For debugging queries, prioritize backend/pipeline files over UI screens."""


# ── Backend Implementations ─────────────────────────────────────────

def _call_ollama(prompt: str, system: str, model: str = "llama3.2:1b",
                 base_url: str = "http://localhost:11434") -> Optional[str]:
    """Call Ollama local LLM."""
    import urllib.request
    import urllib.error

    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "options": {
            "temperature": 0.1,
            "num_predict": 2048,
        },
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{base_url}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("message", {}).get("content", "")
    except (urllib.error.URLError, Exception) as e:
        print(f"[scout] Ollama error: {e}")
        return None


def _call_groq(prompt: str, system: str, model: str = "llama-3.2-1b-preview",
               api_key: str = "") -> Optional[dict]:
    """Call Groq cloud API using requests library."""
    if not api_key:
        return None

    try:
        import requests as _requests
        resp = _requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.1,
                "max_tokens": 1024,
            },
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        tokens = data.get("usage", {}).get("total_tokens", 0)
        return {"content": content, "tokens": tokens}
    except Exception as e:
        err_body = ""
        try:
            err_body = e.response.text[:300] if hasattr(e, 'response') and e.response is not None else ""
        except Exception:
            pass
        print(f"[scout] Groq error: {e} | body: {err_body}")
        return None


def _check_ollama(base_url: str = "http://localhost:11434") -> bool:
    """Quick check if Ollama is running."""
    import urllib.request
    try:
        urllib.request.urlopen(f"{base_url}/api/tags", timeout=2)
        return True
    except Exception:
        return False


def _parse_scout_response(raw: str) -> tuple[List[str], str]:
    """Parse the Scout's JSON response into a list of symbol IDs."""
    # Try to extract JSON from the response (handle markdown wrapping)
    raw = raw.strip()

    # Remove markdown code fences if present
    if raw.startswith("```"):
        raw = re.sub(r'^```(?:json)?\s*', '', raw)
        raw = re.sub(r'\s*```\s*$', '', raw)

    try:
        data = json.loads(raw)
        symbols = data.get("symbols", [])
        reason = data.get("reason", "")
        return symbols, reason
    except json.JSONDecodeError:
        # Try to find JSON in the response
        match = re.search(r'\{[\s\S]*"symbols"[\s\S]*\}', raw)
        if match:
            try:
                data = json.loads(match.group())
                return data.get("symbols", []), data.get("reason", "")
            except json.JSONDecodeError:
                pass

        # Last resort: extract file_path::name patterns
        patterns = re.findall(r'[\w/\\.-]+::\w+', raw)
        return patterns, "parsed from raw text"


# ── Main Scout Class ────────────────────────────────────────────────

class Scout:
    """
    Context-aware symbol ranker using Llama 3.2-1B.

    Pluggable backend: tries Ollama first (offline), falls back to Groq (cloud).
    If neither is available, falls back to keyword-based ranking.

    Usage:
        scout = Scout()
        result = scout.rank_symbols(
            query="How does incident detection work?",
            context="## Project Overview\\n...",
            symbol_list="[function] fetchRSS @ feeds.ts:10\\n...",
        )
        # result.ranked_symbols = ["feeds.ts::fetchRSS", "groq.ts::processIncidents", ...]
    """

    def __init__(
        self,
        groq_api_key: str = "",
        ollama_url: str = "http://localhost:11434",
        ollama_model: str = "llama3.1:8b",
        groq_model: str = "llama-3.1-8b-instant",
        prefer: str = "auto",  # "ollama", "groq", or "auto" (try ollama first)
    ):
        self.groq_api_key = groq_api_key or os.environ.get("GROQ_API_KEY", "")
        self.ollama_url = ollama_url
        self.ollama_model = ollama_model
        self.groq_model = groq_model
        self.prefer = prefer

        # Detect .env file if no key set
        if not self.groq_api_key:
            self.groq_api_key = self._load_env_key()

    def _load_env_key(self) -> str:
        """Try to load GROQ_API_KEY from .env file."""
        for env_path in [".env", "../.env", os.path.join(os.path.dirname(__file__), "..", ".env")]:
            try:
                with open(env_path, "r") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("GROQ_API_KEY="):
                            return line.split("=", 1)[1].strip().strip('"').strip("'")
            except OSError:
                continue
        return ""

    def rank_symbols(
        self,
        query: str,
        context: str,
        symbol_list: str,
        symbol_count: int = 0,
    ) -> ScoutResult:
        """
        Ask the Scout to rank symbols by relevance to the query.

        Args:
            query: The developer's question/task
            context: Multi-layered context from ContextLoader
            symbol_list: Formatted symbol list from ContextLoader.build_symbol_list()
            symbol_count: Total number of symbols

        Returns:
            ScoutResult with ranked symbol identifiers
        """
        user_prompt = _SCOUT_USER_TEMPLATE.format(
            context=context,
            symbols=symbol_list,
            symbol_count=symbol_count or symbol_list.count("\n") + 1,
            query=query,
        )

        start = time.time()
        result = ScoutResult()

        # Try backends in order
        if self.prefer in ("ollama", "auto"):
            response = self._try_ollama(user_prompt)
            if response:
                result.backend = "ollama"
                result.model = self.ollama_model
                symbols, reason = _parse_scout_response(response)
                result.ranked_symbols = symbols
                result.reasoning = reason
                result.elapsed_ms = round((time.time() - start) * 1000, 1)
                print(f"[scout] Ollama returned {len(symbols)} symbols in {result.elapsed_ms}ms")
                return result

        if self.prefer in ("groq", "auto"):
            response = self._try_groq(user_prompt)
            if response:
                result.backend = "groq"
                result.model = self.groq_model
                result.tokens_used = response.get("tokens", 0)
                symbols, reason = _parse_scout_response(response["content"])
                result.ranked_symbols = symbols
                result.reasoning = reason
                result.elapsed_ms = round((time.time() - start) * 1000, 1)
                print(f"[scout] Groq returned {len(symbols)} symbols in {result.elapsed_ms}ms "
                      f"({result.tokens_used} tokens)")
                return result

        # Fallback: no LLM available
        result.backend = "fallback"
        result.reasoning = "No LLM backend available, using keyword fallback"
        result.elapsed_ms = round((time.time() - start) * 1000, 1)
        print(f"[scout] No backend available, falling back to keyword search")
        return result

    def _try_ollama(self, prompt: str) -> Optional[str]:
        """Try Ollama backend."""
        if not _check_ollama(self.ollama_url):
            return None
        return _call_ollama(prompt, _SCOUT_SYSTEM, self.ollama_model, self.ollama_url)

    def _try_groq(self, prompt: str) -> Optional[dict]:
        """Try Groq backend."""
        if not self.groq_api_key:
            return None
        return _call_groq(prompt, _SCOUT_SYSTEM, self.groq_model, self.groq_api_key)

    def is_available(self) -> dict:
        """Check which backends are available."""
        return {
            "ollama": _check_ollama(self.ollama_url),
            "groq": bool(self.groq_api_key),
        }
