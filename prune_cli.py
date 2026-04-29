"""
prune_cli.py — PruneTool interactive CLI
=========================================
Usage:
  prune chat               Start interactive chat (auto-routes models)
  prune model <alias>      Lock to a specific model alias
  prune models             List configured models + daily usage
  prune status             Show gateway status + active model

The Broker picks the right LLM on every prompt:
  1. Groq llama-instant classifies prompt complexity (simple/medium/heavy)
  2. Checks daily token usage against dailyTokenGoal (warns at 90%, pivots at 95%)
  3. Checks pruned context size against model maxContext
  4. Falls back through fallback_order if primary model is unavailable
"""

from __future__ import annotations

import json
import os
import sys
import time
import datetime
import hashlib
import threading
from pathlib import Path
from typing import Optional

try:
    import httpx
except ImportError:
    print("ERROR: httpx not installed. Run: pip install httpx")
    sys.exit(1)

# ── Paths ──────────────────────────────────────────────────────────────
PRUNETOOL_DIR   = Path.home() / ".prunetool"
ENV_FILE        = PRUNETOOL_DIR / ".env"
STATS_FILE      = PRUNETOOL_DIR / "daily_stats.json"
ACTIVE_MODEL_FILE = PRUNETOOL_DIR / "active_model.txt"

# Config search order: user home dir first, then binary dir, then cwd
_HERE = Path(__file__).resolve().parent
LLM_CONFIG_PATHS = [
    PRUNETOOL_DIR / "llms_prunetoolfinder.js",   # user's own copy (~/.prunetool/)
    _HERE / "llms_prunetoolfinder.js",            # shipped default (next to binary)
]

GATEWAY_URL = "http://localhost:8000"
GATEWAY_TIMEOUT = 5.0


# ── Env loader ────────────────────────────────────────────────────────
def _load_env() -> dict:
    env: dict = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env


def _get_key(provider: str, env: dict) -> Optional[str]:
    key_map = {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai":    "OPENAI_API_KEY",
        "groq":      "GROQ_API_KEY",
        "gemini":    "GEMINI_API_KEY",
    }
    env_key = key_map.get(provider.lower())
    if not env_key:
        return None
    return env.get(env_key) or os.environ.get(env_key)


# ── LLM Config loader ─────────────────────────────────────────────────
import re as _re

def _infer_provider(model_id: str) -> str:
    m = model_id.lower()
    if m.startswith("claude"):
        return "anthropic"
    if m.startswith(("gpt-", "o1", "o3", "o4", "text-davinci")):
        return "openai"
    if m.startswith("gemini"):
        return "gemini"
    return "groq"


def _parse_js_config(text: str) -> dict:
    """Strip JS module syntax and comments, then parse the object as JSON."""
    # Remove line comments
    text = _re.sub(r"//[^\n]*", "", text)
    # Remove block comments
    text = _re.sub(r"/\*.*?\*/", "", text, flags=_re.DOTALL)
    # Extract the object literal after module.exports =
    m = _re.search(r"module\.exports\s*=\s*(\{.*\})", text, flags=_re.DOTALL)
    if not m:
        raise ValueError("No module.exports found")
    obj = m.group(1).strip().rstrip(";")
    # Remove trailing commas before } or ]
    obj = _re.sub(r",\s*([}\]])", r"\1", obj)
    return json.loads(obj)


# Known context windows — users never need to set these manually.
# Keyed by model ID prefix (longest match wins).
_MODEL_MAX_CONTEXT: list[tuple[str, int]] = [
    ("claude",          200_000),
    ("gemini",        1_000_000),
    ("gpt-4o",          128_000),
    ("gpt-4",           128_000),
    ("gpt-3.5",          16_385),
    ("o1",              200_000),
    ("o3",              200_000),
    ("o4",              200_000),
    ("llama-3.1-405",   131_072),
    ("llama-3.1",       131_072),
    ("llama-3.3",       128_000),
    ("llama-3",         128_000),
    ("mixtral",          32_768),
]

def _lookup_max_context(model_id: str) -> int:
    m = model_id.lower()
    for prefix, ctx in _MODEL_MAX_CONTEXT:
        if m.startswith(prefix):
            return ctx
    return 128_000  # safe default


# ── Live context window cache ──────────────────────────────────────────
_CTX_CACHE_FILE = PRUNETOOL_DIR / "model_contexts.json"
_CTX_CACHE_TTL  = 86_400  # 24 hours


def _load_context_cache() -> dict:
    if not _CTX_CACHE_FILE.exists():
        return {}
    try:
        data = json.loads(_CTX_CACHE_FILE.read_text(encoding="utf-8"))
        if time.time() - data.get("_fetched_at", 0) > _CTX_CACHE_TTL:
            return {}
        return data
    except Exception:
        return {}


def _save_context_cache(ctx_map: dict):
    PRUNETOOL_DIR.mkdir(parents=True, exist_ok=True)
    ctx_map["_fetched_at"] = time.time()
    _CTX_CACHE_FILE.write_text(json.dumps(ctx_map, indent=2), encoding="utf-8")


def _fetch_provider_contexts(env: dict) -> dict:
    """
    Fetch live context window sizes from provider /v1/models endpoints.
    Returns {model_id: context_window} for all models found.
    Silently skips any provider whose API call fails.
    """
    results: dict = {}

    # OpenAI + Groq + Gemini — all speak OpenAI-compatible /v1/models
    openai_like = [
        ("openai", "https://api.openai.com/v1/models",   "OPENAI_API_KEY",  "context_window"),
        ("groq",   "https://api.groq.com/openai/v1/models", "GROQ_API_KEY", "context_window"),
    ]
    for provider, url, key_name, field in openai_like:
        api_key = env.get(key_name) or os.environ.get(key_name)
        if not api_key:
            continue
        try:
            resp = httpx.get(url, headers={"Authorization": f"Bearer {api_key}"}, timeout=8.0)
            for m in resp.json().get("data", []):
                ctx = m.get(field)
                if m.get("id") and ctx:
                    results[m["id"]] = int(ctx)
        except Exception:
            pass

    # Anthropic — /v1/models returns context_window per model
    anthropic_key = env.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    if anthropic_key:
        try:
            resp = httpx.get(
                "https://api.anthropic.com/v1/models",
                headers={"x-api-key": anthropic_key, "anthropic-version": "2023-06-01"},
                timeout=8.0,
            )
            for m in resp.json().get("data", []):
                ctx = m.get("context_window")
                if m.get("id") and ctx:
                    results[m["id"]] = int(ctx)
        except Exception:
            pass

    # Gemini — /v1beta/models uses inputTokenLimit
    gemini_key = env.get("GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if gemini_key:
        try:
            resp = httpx.get(
                f"https://generativelanguage.googleapis.com/v1beta/models?key={gemini_key}",
                timeout=8.0,
            )
            for m in resp.json().get("models", []):
                model_id = m.get("name", "").replace("models/", "")
                ctx = m.get("inputTokenLimit")
                if model_id and ctx:
                    results[model_id] = int(ctx)
        except Exception:
            pass

    return results


def _get_live_context(model_id: str, live: dict) -> Optional[int]:
    """Exact match first, then check if any live key is a prefix/suffix match."""
    if model_id in live:
        return live[model_id]
    # providers sometimes return versioned IDs like "gpt-4o-2024-08-06"
    # match by stripping date suffixes
    m = model_id.lower()
    for live_id, ctx in live.items():
        if m.startswith(live_id.lower()) or live_id.lower().startswith(m):
            return ctx
    return None


def _normalize_js_models(config: dict, live: dict | None = None) -> dict:
    """Convert .js model entries to the internal format the broker expects."""
    complexity_map = {"simple": "simple", "medium": "medium",
                      "complex": "heavy", "heavy": "heavy"}
    live = live or {}
    normalized = []
    for m in config.get("models", []):
        model_api_id = m.get("model", m.get("id", ""))
        provider = m.get("provider") or _infer_provider(model_api_id)
        raw_cx = m.get("complexity", "medium")
        if isinstance(raw_cx, str):
            raw_cx = [raw_cx]
        complexity = [complexity_map.get(c, c) for c in raw_cx]
        # Priority: 1) user set in .js  2) live API  3) hardcoded table
        max_ctx = (
            m.get("maxContext")
            or _get_live_context(model_api_id, live)
            or _lookup_max_context(model_api_id)
        )
        normalized.append({
            "id":            m.get("id", model_api_id),
            "model":         model_api_id,
            "label":         m.get("label", m.get("id", "")),
            "provider":      provider,
            "complexity":    complexity,
            "dailyTokenGoal": m.get("dailyTokenGoal", 0),
            "maxContext":    max_ctx,
            "priority":      m.get("priority", 1),
        })
    result = dict(config)
    result["models"] = normalized
    return result


def _load_llm_config(env: dict | None = None) -> dict:
    # Load live context cache (or fetch fresh if stale)
    live = _load_context_cache()
    if not live and env:
        live = _fetch_provider_contexts(env)
        if live:
            _save_context_cache(live)
            print(f"[prune] Fetched live context windows for {len(live)} models.")

    for path in LLM_CONFIG_PATHS:
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8")
            if path.suffix == ".js":
                return _normalize_js_models(_parse_js_config(text), live)
            return json.loads(text)
        except Exception:
            pass
    print("[prune] WARNING: llms_prunetoolfinder.js not found. Using defaults.")
    return _default_config()


def _default_config() -> dict:
    return {
        "router_model": "llama-3.1-8b-instant",
        "router_provider": "groq",
        "fallback_order": ["groq", "anthropic", "openai", "gemini"],
        "models": [
            {"id": "llama-3.1-8b-instant", "label": "Groq Llama 8B",  "provider": "groq",      "complexity": ["simple"],          "maxContext": 128000, "dailyTokenGoal": 500000, "priority": 1},
            {"id": "claude-sonnet-4-6",    "label": "Claude Sonnet",   "provider": "anthropic", "complexity": ["medium", "heavy"], "maxContext": 200000, "dailyTokenGoal": 50000,  "priority": 1},
            {"id": "gemini-2.0-flash",     "label": "Gemini Flash",    "provider": "gemini",    "complexity": ["simple", "medium"],"maxContext": 1000000,"dailyTokenGoal": 150000, "priority": 2},
        ]
    }


# ── Daily stats ───────────────────────────────────────────────────────
class DailyStats:
    def __init__(self):
        self._data: dict = self._load()

    def _load(self) -> dict:
        today = str(datetime.date.today())
        if STATS_FILE.exists():
            try:
                data = json.loads(STATS_FILE.read_text(encoding="utf-8"))
                if data.get("date") == today:
                    return data
            except Exception:
                pass
        return {"date": today, "usage": {}}

    def _save(self):
        PRUNETOOL_DIR.mkdir(parents=True, exist_ok=True)
        STATS_FILE.write_text(json.dumps(self._data, indent=2), encoding="utf-8")

    def tokens_used(self, model_id: str) -> int:
        return self._data["usage"].get(model_id, {}).get("total", 0)

    def record(self, model_id: str, tokens_in: int, tokens_out: int):
        today = str(datetime.date.today())
        if self._data.get("date") != today:
            self._data = {"date": today, "usage": {}}
        usage = self._data["usage"].setdefault(model_id, {"in": 0, "out": 0, "total": 0})
        usage["in"]    += tokens_in
        usage["out"]   += tokens_out
        usage["total"] += tokens_in + tokens_out
        self._save()

    def usage_pct(self, model_id: str, daily_goal: int) -> float:
        if daily_goal <= 0:
            return 0.0
        return self.tokens_used(model_id) / daily_goal * 100


# ── Broker ────────────────────────────────────────────────────────────
class Broker:
    WARN_PCT  = 90.0
    BLOCK_PCT = 95.0

    def __init__(self, config: dict, env: dict, stats: DailyStats):
        self.config  = config
        self.env     = env
        self.stats   = stats
        self.models  = config.get("models", [])
        self.fallback_order = config.get("fallback_order", ["groq", "anthropic", "openai", "gemini"])

    def available_models(self) -> list[dict]:
        return [m for m in self.models if _get_key(m["provider"], self.env)]

    def classify_prompt(self, prompt: str, context_tokens: int) -> str:
        """
        Use Groq llama-instant to classify complexity.
        Falls back to keyword heuristic if Groq key missing.
        """
        groq_key = _get_key("groq", self.env)
        if groq_key:
            return self._classify_via_groq(prompt, context_tokens, groq_key)
        return self._classify_heuristic(prompt, context_tokens)

    def _classify_via_groq(self, prompt: str, context_tokens: int, api_key: str) -> str:
        router_model = self.config.get("router_model", "llama-3.1-8b-instant")
        system = (
            "You are a task complexity classifier for a coding AI. "
            "Classify the user's coding request as exactly one word: simple, medium, or heavy.\n"
            "- simple: typo fix, rename, add one line, small bug\n"
            "- medium: new function, small feature, explain one file\n"
            "- heavy: architecture, refactor multiple files, explain entire system, design decisions\n"
            f"Context size: {context_tokens} tokens (>1000 tokens suggests medium/heavy).\n"
            "Reply with ONLY the single word: simple, medium, or heavy."
        )
        try:
            resp = httpx.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": router_model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user",   "content": prompt[:500]},
                    ],
                    "max_tokens": 5,
                    "temperature": 0,
                },
                timeout=8.0,
            )
            word = resp.json()["choices"][0]["message"]["content"].strip().lower()
            if word in ("simple", "medium", "heavy"):
                return word
        except Exception:
            pass
        return self._classify_heuristic(prompt, context_tokens)

    def _classify_heuristic(self, prompt: str, context_tokens: int) -> str:
        heavy_kw = {"architecture", "entire", "refactor", "design", "explain", "why does",
                    "how does", "overview", "all files", "whole", "system", "pipeline"}
        low = prompt.lower()
        words = len(prompt.split())
        if any(kw in low for kw in heavy_kw) or context_tokens > 2000:
            return "heavy"
        if words > 30 or context_tokens > 500:
            return "medium"
        return "simple"

    def pick(self, complexity: str, context_tokens: int) -> tuple[Optional[dict], list[str]]:
        """
        Returns (chosen_model_dict, list_of_warning_messages).
        Tries candidates for the complexity tier in priority order.
        Falls back across tiers if nothing available.
        """
        warnings: list[str] = []
        candidates = [m for m in self.available_models() if complexity in m.get("complexity", [])]
        candidates.sort(key=lambda m: m.get("priority", 99))

        for model in candidates:
            model_id  = model["id"]
            goal      = model.get("dailyTokenGoal", 0)
            max_ctx   = model.get("maxContext", 128000)
            pct       = self.stats.usage_pct(model_id, goal) if goal else 0.0
            used      = self.stats.tokens_used(model_id)

            if pct >= self.BLOCK_PCT:
                warnings.append(
                    f"[prune] {model['label']} at {pct:.0f}% daily goal "
                    f"({used:,}/{goal:,} tokens) — skipping."
                )
                continue

            if context_tokens > max_ctx:
                warnings.append(
                    f"[prune] {model['label']} maxContext {max_ctx:,} < "
                    f"context {context_tokens:,} tokens — skipping."
                )
                continue

            if pct >= self.WARN_PCT:
                warnings.append(
                    f"[prune] Warning: {model['label']} at {pct:.0f}% daily goal. "
                    f"Continuing — will pivot when it hits 95%."
                )

            return model, warnings

        # Nothing matched — try any available model with enough context
        warnings.append(
            f"[prune] No {complexity} model available. Falling back to any model with headroom."
        )
        for model in self.available_models():
            if context_tokens <= model.get("maxContext", 128000):
                used = self.stats.tokens_used(model["id"])
                goal = model.get("dailyTokenGoal", 0)
                pct  = self.stats.usage_pct(model["id"], goal) if goal else 0.0
                if pct < self.BLOCK_PCT:
                    return model, warnings

        return None, warnings + ["[prune] ERROR: All models exhausted or over daily limit."]


# ── Gateway helpers ────────────────────────────────────────────────────
def _gateway_up() -> bool:
    try:
        httpx.get(f"{GATEWAY_URL}/health", timeout=GATEWAY_TIMEOUT)
        return True
    except Exception:
        return False


def _project_index_ready() -> bool:
    """
    Check if .prunetool/last_scan.json exists — written at end of every
    successful scan. If it's there, all other index files are guaranteed to exist.
    """
    croot = Path(os.environ.get("PRUNE_CODEBASE_ROOT", Path.cwd()))
    return (croot / ".prunetool" / "last_scan.json").exists()


def _last_scan_info() -> dict:
    """Return last_scan.json contents, or empty dict if not found."""
    croot = Path(os.environ.get("PRUNE_CODEBASE_ROOT", Path.cwd()))
    path  = croot / ".prunetool" / "last_scan.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _trigger_project_scan() -> bool:
    """POST /re-scan to gateway. Returns True if accepted."""
    try:
        resp = httpx.post(f"{GATEWAY_URL}/re-scan", json={}, timeout=10.0)
        return resp.status_code == 200
    except Exception:
        return False


def _wait_for_scan(timeout: float = 180.0):
    """Poll /scan-status and print live progress until stage == complete."""
    deadline = time.time() + timeout
    last_msg = ""
    while time.time() < deadline:
        time.sleep(3)
        try:
            data  = httpx.get(f"{GATEWAY_URL}/scan-status", timeout=5.0).json()
            stage = data.get("stage", "idle")
            files = data.get("files_found", 0)
            syms  = data.get("symbols_found", 0)
            ann   = data.get("annotated", 0)
            total = data.get("total_to_annotate", 0)

            if stage == "scanning":
                msg = f"  Indexing files... {files} found"
            elif stage == "building_map":
                msg = f"  Building folder map... {files} files, {syms} symbols"
            elif stage == "annotating":
                pct = int(ann / total * 100) if total else 0
                msg = f"  Annotating files... {ann}/{total} ({pct}%)"
            elif stage == "complete":
                print(f"  Scan complete — {files} files, {syms} symbols indexed.\n")
                return
            else:
                msg = f"  {stage}..."

            if msg != last_msg:
                print(msg, flush=True)
                last_msg = msg
        except Exception:
            pass
    print("  Scan timed out — context may be partial.\n")


def _load_project_context() -> str:
    """
    Read terminal_context.md from the project's .prunetool/ folder.
    Returns the content as a string, or empty string if not found.
    """
    croot = Path(os.environ.get("PRUNE_CODEBASE_ROOT", Path.cwd()))
    ctx_path = croot / ".prunetool" / "terminal_context.md"
    if ctx_path.exists():
        return ctx_path.read_text(encoding="utf-8", errors="replace")
    return ""


def _get_pruned_context(prompt: str) -> tuple[str, int]:
    """Returns (context_text, token_estimate). Empty string if gateway down."""
    try:
        resp = httpx.post(
            f"{GATEWAY_URL}/prune",
            json={"query": prompt},
            timeout=15.0,
        )
        data = resp.json()
        ctx  = data.get("context", "")
        toks = data.get("tokens_used", len(ctx) // 4)
        return ctx, toks
    except Exception:
        return "", 0


# ── CLI backend (for subscription users without API keys) ─────────────
import shutil as _shutil

# Maps provider → (cli_command, prompt_flag, model_flag)
_PROVIDER_CLI: dict[str, tuple[str, str, str]] = {
    "anthropic": ("claude",  "-p", "--model"),
    "gemini":    ("gemini",  "-p", "--model"),
}


def _find_cli(provider: str) -> Optional[str]:
    """Return full path to provider CLI if installed, else None."""
    cli_name = _PROVIDER_CLI.get(provider, (None,))[0]
    if not cli_name:
        return None
    return _shutil.which(cli_name)


def _detect_backends(env: dict, config: dict) -> dict[str, str]:
    """
    For each model return how it can be called: "api", "cli", or "none".
    Used by model picker to show/hide models and label their access method.
    """
    result = {}
    for m in config.get("models", []):
        provider = m["provider"]
        if _find_cli(provider):
            result[m["id"]] = "cli"       # CLI beats API key — subscription user
        elif _get_key(provider, env):
            result[m["id"]] = "api"
        else:
            result[m["id"]] = "none"
    return result


def _call_cli(model: dict, messages: list, stats: DailyStats) -> tuple[int, int]:
    """
    Call provider CLI (claude / gemini) with -p flag.
    Builds a plain-text prompt from the message history and streams output.
    Returns (tokens_in, tokens_out).
    """
    provider  = model["provider"]
    alias_id  = model["id"]
    model_id  = model.get("model", model["id"])
    cli_path  = _find_cli(provider)
    _, p_flag, m_flag = _PROVIDER_CLI[provider]

    if not cli_path:
        print(f"[prune] ERROR: {provider} CLI not found.")
        return 0, 0

    # Build single text prompt: system context + conversation history
    parts = []
    for msg in messages:
        role    = msg["role"]
        content = msg.get("content", "")
        if role == "system":
            parts.append(f"[System]\n{content}\n")
        elif role == "user":
            parts.append(f"[User]\n{content}\n")
        elif role == "assistant":
            parts.append(f"[Assistant]\n{content}\n")
    full_prompt = "\n".join(parts)

    tokens_in  = len(full_prompt) // 4
    tokens_out = 0

    cmd = [cli_path, p_flag, full_prompt, m_flag, model_id]

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
        )
        for char in iter(lambda: proc.stdout.read(1), ""):
            print(char, end="", flush=True)
            tokens_out += 1
        proc.wait()
        if proc.returncode != 0:
            err = proc.stderr.read()
            if err:
                print(f"\n[prune] CLI error: {err[:200]}")
    except Exception as e:
        print(f"\n[prune] CLI call failed: {e}")

    print()
    tokens_out = tokens_out // 4  # chars to rough token estimate
    stats.record(alias_id, tokens_in, tokens_out)
    return tokens_in, tokens_out


# ── LLM call (streaming) ──────────────────────────────────────────────
PROVIDER_ENDPOINTS = {
    "anthropic": "https://api.anthropic.com/v1/messages",
    "openai":    "https://api.openai.com/v1/chat/completions",
    "groq":      "https://api.groq.com/openai/v1/chat/completions",
    "gemini":    "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
}


def _stream_response(model: dict, messages: list, env: dict, stats: DailyStats):
    """Stream response to stdout. Returns (tokens_in, tokens_out)."""
    provider  = model["provider"]
    model_id  = model.get("model", model["id"])  # real API model ID
    alias_id  = model["id"]                       # used for stats tracking
    api_key   = _get_key(provider, env)
    endpoint  = PROVIDER_ENDPOINTS.get(provider)

    # Check CLI first — subscription users don't have API keys
    if _find_cli(provider):
        return _call_cli(model, messages, stats)

    if not api_key:
        print(f"[prune] ERROR: No {provider} CLI found and no API key set.")
        print(f"         Option 1: Install the {provider} CLI and log in.")
        print(f"         Option 2: Add {provider.upper()}_API_KEY to ~/.prunetool/.env")
        return 0, 0

    if not endpoint:
        print(f"[prune] ERROR: Unknown provider '{provider}'.")
        return 0, 0

    headers = {"Content-Type": "application/json"}
    if provider == "anthropic":
        headers["x-api-key"]         = api_key
        headers["anthropic-version"] = "2023-06-01"
        # Anthropic uses different message format
        system_msg = next((m["content"] for m in messages if m["role"] == "system"), "")
        user_msgs  = [m for m in messages if m["role"] != "system"]
        payload = {
            "model":      model_id,
            "max_tokens": 4096,
            "system":     system_msg,
            "messages":   user_msgs,
            "stream":     True,
        }
    else:
        headers["Authorization"] = f"Bearer {api_key}"
        payload = {
            "model":    model_id,
            "messages": messages,
            "stream":   True,
        }

    tokens_in  = sum(len(m.get("content", "")) // 4 for m in messages)
    tokens_out = 0

    try:
        with httpx.stream("POST", endpoint, headers=headers, json=payload, timeout=120.0) as resp:
            if resp.status_code != 200:
                body = resp.read().decode()
                print(f"\n[prune] API error {resp.status_code}: {body[:300]}")
                return tokens_in, 0

            for line in resp.iter_lines():
                if not line or line == "data: [DONE]":
                    continue
                if line.startswith("data: "):
                    line = line[6:]
                try:
                    chunk = json.loads(line)
                    # Anthropic
                    if "delta" in chunk and "text" in chunk.get("delta", {}):
                        text = chunk["delta"]["text"]
                        print(text, end="", flush=True)
                        tokens_out += len(text) // 4
                    # OpenAI/Groq/Gemini
                    elif "choices" in chunk:
                        delta = chunk["choices"][0].get("delta", {})
                        text  = delta.get("content", "")
                        if text:
                            print(text, end="", flush=True)
                            tokens_out += len(text) // 4
                except Exception:
                    pass

    except httpx.ReadTimeout:
        print("\n[prune] Request timed out.")
    except Exception as e:
        print(f"\n[prune] Stream error: {e}")

    print()  # newline after response
    stats.record(alias_id, tokens_in, tokens_out)
    return tokens_in, tokens_out


# ── Active model persistence ──────────────────────────────────────────
def _get_active_model_alias() -> str:
    if ACTIVE_MODEL_FILE.exists():
        return ACTIVE_MODEL_FILE.read_text(encoding="utf-8").strip()
    return "auto"


def _set_active_model_alias(alias: str):
    PRUNETOOL_DIR.mkdir(parents=True, exist_ok=True)
    ACTIVE_MODEL_FILE.write_text(alias, encoding="utf-8")


def _resolve_alias(alias: str, config: dict, env: dict) -> Optional[dict]:
    """Map alias like 'sonnet', 'groq', 'haiku' to a model dict."""
    alias_map = {
        "sonnet":  "claude-sonnet-4-6",
        "opus":    "claude-opus-4-7",
        "haiku":   "claude-haiku-4-5-20251001",
        "gpt-4o":  "gpt-4o",
        "codex":   "gpt-4.1",
        "groq":    "llama-3.3-70b-versatile",
        "gemini":  "gemini-2.0-flash",
    }
    model_id = alias_map.get(alias.lower(), alias)
    for m in config.get("models", []):
        if m["id"] == model_id:
            return m
    # Build a minimal entry for unknown model IDs so we can still call them
    return None


# ── Model picker (Copilot-style) ──────────────────────────────────────
def _model_picker(config: dict, env: dict, stats: DailyStats) -> str:
    """
    Show a numbered model list and let the user pick one.
    Returns the chosen model alias, or "auto" if user just hits Enter.
    """
    backends  = _detect_backends(env, config)
    available = [m for m in config.get("models", []) if backends.get(m["id"]) != "none"]

    if not available:
        print("  [prune] No models available.")
        print("          Install a provider CLI (claude / gemini) or add API keys to ~/.prunetool/.env")
        return "auto"

    print("\n  Select a model (or press Enter for auto-routing):\n")
    print(f"  {'#':<4} {'Model':<24} {'Via':<6} {'For':<10} {'Used today':>12}  {'Limit':>10}  {'%':>6}")
    print("  " + "-" * 80)

    for i, m in enumerate(available, 1):
        used    = stats.tokens_used(m["id"])
        goal    = m.get("dailyTokenGoal", 0)
        pct     = stats.usage_pct(m["id"], goal) if goal else 0.0
        tiers   = "/".join(m.get("complexity", []))
        via     = backends.get(m["id"], "?")   # "cli" or "api"
        bar     = f"{pct:>5.1f}%"
        limit_flag = "  [near limit]" if pct >= 90 else ""
        print(f"  {i:<4} {m['label']:<24} {via:<6} {tiers:<10} {used:>12,}  {goal:>10,}  {bar}{limit_flag}")

    print(f"\n  {len(available)+1:<4} {'auto':<24} {'(let PruneTool decide per prompt)'}")
    print()

    while True:
        try:
            raw = input("  Pick [1-{}/Enter=auto]: ".format(len(available))).strip()
        except (EOFError, KeyboardInterrupt):
            return "auto"

        if raw == "":
            print("  -> Auto-routing enabled. PruneTool will pick the best model per prompt.\n")
            return "auto"

        if raw.isdigit():
            idx = int(raw)
            if 1 <= idx <= len(available):
                chosen = available[idx - 1]
                print(f"  -> {chosen['label']}  (switch anytime with /model <name>)\n")
                return chosen["id"]
            if idx == len(available) + 1:
                print("  -> Auto-routing enabled.\n")
                return "auto"

        print(f"  Please enter a number between 1 and {len(available)+1}, or press Enter.")


# ── Commands ──────────────────────────────────────────────────────────
def cmd_models(config: dict, env: dict, stats: DailyStats):
    print("\nConfigured models:\n")
    print(f"  {'Label':<22} {'Provider':<12} {'Complexity':<22} {'Used today':>12}  {'Goal':>10}  {'%':>6}  Context")
    print("  " + "-" * 100)
    for m in config.get("models", []):
        key_ok    = bool(_get_key(m["provider"], env))
        used      = stats.tokens_used(m["id"])
        goal      = m.get("dailyTokenGoal", 0)
        pct       = stats.usage_pct(m["id"], goal) if goal else 0.0
        max_ctx   = m.get("maxContext", 0)
        tiers     = "/".join(m.get("complexity", []))
        key_flag  = " " if key_ok else " [NO KEY]"
        bar = "#" * int(pct / 10) + "." * (10 - int(pct / 10))
        print(f"  {m['label']:<22} {m['provider']:<12} {tiers:<22} {used:>12,}  {goal:>10,}  {pct:>5.1f}%  {max_ctx:,}{key_flag}")
    active = _get_active_model_alias()
    print(f"\n  Active model: {active}\n")


def cmd_model(alias: str, config: dict, env: dict):
    if alias == "auto":
        _set_active_model_alias("auto")
        print(f"[prune] Auto-routing enabled. Groq will classify each prompt.")
        return
    model = _resolve_alias(alias, config, env)
    if not model:
        print(f"[prune] Unknown model '{alias}'. Run 'prune models' to see options.")
        sys.exit(1)
    if not _get_key(model["provider"], env):
        print(f"[prune] No API key for {model['provider']}. Add {model['provider'].upper()}_API_KEY to ~/.prunetool/.env")
        sys.exit(1)
    _set_active_model_alias(alias)
    print(f"[prune] Locked to {model['label']} ({model['id']})")


def cmd_status(config: dict, env: dict):
    active = _get_active_model_alias()
    gw_up  = _gateway_up()
    print(f"\n  Gateway   : {'UP  (context injection active)' if gw_up else 'DOWN (plain LLM mode — run prunetool.exe first)'}")
    print(f"  Model     : {active}")
    configured = [m["provider"] for m in config.get("models", []) if _get_key(m["provider"], env)]
    print(f"  Keys      : {', '.join(set(configured)) or 'none'}")
    print()


def _scan_age_seconds() -> Optional[float]:
    """
    Returns how many seconds ago the last scan ran.
    Returns None if last_scan.json doesn't exist or can't be parsed.
    """
    info = _last_scan_info()
    indexed_at = info.get("indexed_at")
    if not indexed_at:
        return None
    try:
        from datetime import timezone
        ts = datetime.datetime.fromisoformat(indexed_at)
        # make aware if naive
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        now = datetime.datetime.now(timezone.utc)
        return (now - ts).total_seconds()
    except Exception:
        return None


def _cmd_describe(gw_up: bool) -> str:
    """
    /describe handler — checks scan freshness, optionally rescans,
    then loads terminal_context.md into session cache.
    Returns the project_context string (empty string on failure).
    """
    if not gw_up:
        print("[prune] Gateway is not running — cannot load project context.")
        print("        Start prunetool.exe first, then type /describe again.")
        return ""

    # ── No index at all → auto scan ───────────────────────────────────
    if not _project_index_ready():
        croot = os.environ.get("PRUNE_CODEBASE_ROOT", str(Path.cwd()))
        print(f"[prune] No project index found for: {croot}")
        print("[prune] Running project scan now...\n")
        if _trigger_project_scan():
            _wait_for_scan()
        else:
            print("[prune] Could not trigger scan — open http://localhost:8000 and click Scan Project.")
            return ""

    # ── Index exists — check age ──────────────────────────────────────
    age = _scan_age_seconds()
    info = _last_scan_info()
    file_count = info.get("file_count", "?")
    sym_count  = info.get("total_symbols", "?")

    if age is not None and age > 3600:
        hours = int(age // 3600)
        mins  = int((age % 3600) // 60)
        age_str = f"{hours}h {mins}m ago" if hours else f"{mins}m ago"
        print(f"[prune] Last scan was {age_str}  ({file_count} files, {sym_count:,} symbols)")
        try:
            answer = input("[prune] Rescan project before loading context? (y/n): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "n"
        if answer == "y":
            print("[prune] Rescanning...\n")
            if _trigger_project_scan():
                _wait_for_scan()
            else:
                print("[prune] Scan failed — loading existing context.")
    else:
        if age is not None:
            mins = int(age // 60)
            print(f"[prune] Project index is fresh ({mins}m old) — {file_count} files, {sym_count:,} symbols")

    # ── Load context ──────────────────────────────────────────────────
    print("[prune] Loading project context... ", end="", flush=True)
    ctx = _load_project_context()
    if ctx:
        tok_est = len(ctx) // 4
        print(f"done (~{tok_est:,} tokens)  — project context is now active for this session.\n")
    else:
        print("failed — terminal_context.md not found. Try rescanning.")
    return ctx


def _launch_gateway_window():
    """
    Open a new terminal window running prunetool.exe so the user can see
    gateway logs live. prunetool.exe sits next to prune.exe in the same folder.
    """
    # Find prunetool.exe next to this binary (dist folder) or next to this script
    candidates = [
        Path(sys.executable).parent / "prunetool.exe",   # inside PyInstaller dist
        Path(__file__).resolve().parent / "prunetool.exe",  # dev mode
    ]
    gateway_exe = next((p for p in candidates if p.exists()), None)

    if not gateway_exe:
        print("[prune] Could not find prunetool.exe — start it manually.")
        return

    # /k keeps the window open so user can read logs after gateway stops
    subprocess.Popen(
        f'start "PruneTool Gateway" /k "{gateway_exe}"',
        shell=True,
    )


def cmd_chat(config: dict, env: dict, stats: DailyStats):
    broker = Broker(config, env, stats)
    gw_up  = _gateway_up()

    print("\n  PruneTool Chat")
    print("  " + "=" * 40)

    # ── Step 1: ensure gateway is running ─────────────────────────────
    if not gw_up:
        print("  [prune] Gateway not running — starting it now...\n")
        _launch_gateway_window()
        for i in range(20):
            time.sleep(1)
            if _gateway_up():
                print("  [prune] Gateway ready.\n")
                gw_up = True
                break
            print(f"  [prune] Waiting for gateway{'.' * ((i % 3) + 1)}   ", end="\r")
        if not gw_up:
            print("\n  [prune] Gateway did not start — continuing without codebase context.\n")

    # ── Step 2: ensure a project index exists (first-time only) ──────
    project_context = ""
    if gw_up:
        if not _project_index_ready():
            croot = os.environ.get("PRUNE_CODEBASE_ROOT", str(Path.cwd()))
            print(f"  [prune] No project index found for: {croot}")
            print(f"  [prune] Running first-time project scan...\n")
            if _trigger_project_scan():
                _wait_for_scan()
            else:
                print("  [prune] Could not trigger scan — open http://localhost:8000 and click Scan Project.\n")

    # Copilot-style model picker on every session start
    chosen_alias = _model_picker(config, env, stats)
    _set_active_model_alias(chosen_alias)
    active_alias = chosen_alias

    if active_alias == "auto":
        print("  Type /describe to load project context, /model <name> to switch, /quit to exit\n")
    else:
        print("  Type /describe to load project context, /model auto for auto-routing, /quit to exit\n")

    history: list[dict] = []

    while True:
        try:
            prompt = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[prune] Bye.")
            break

        if not prompt:
            continue

        # Inline commands inside chat
        if prompt.startswith("/model "):
            alias = prompt.split(None, 1)[1].strip()
            cmd_model(alias, config, env)
            active_alias = _get_active_model_alias()
            continue
        if prompt in ("/quit", "/exit", "/q"):
            print("[prune] Bye.")
            break
        if prompt == "/models":
            cmd_models(config, env, stats)
            continue
        if prompt == "/status":
            cmd_status(config, env)
            continue
        if prompt == "/clear":
            history.clear()
            print("[prune] Conversation history cleared.")
            continue

        if prompt == "/describe":
            project_context = _cmd_describe(gw_up)
            continue

        # ── Get pruned context ────────────────────────────────────────
        ctx_text, ctx_tokens = ("", 0)
        if gw_up:
            ctx_text, ctx_tokens = _get_pruned_context(prompt)

        # ── Pick model ────────────────────────────────────────────────
        if active_alias == "auto":
            complexity = broker.classify_prompt(prompt, ctx_tokens)
            chosen, warnings = broker.pick(complexity, ctx_tokens)
            for w in warnings:
                print(w)
            if not chosen:
                print("[prune] No model available. Check your API keys and daily limits.")
                continue
            print(f"[auto -> {chosen['label']}]  complexity={complexity}  context={ctx_tokens} tokens")
        else:
            chosen = _resolve_alias(active_alias, config, env)
            if not chosen:
                print(f"[prune] Model '{active_alias}' not found in config. Run /models.")
                continue
            complexity = "medium"
            # Still check context vs maxContext
            max_ctx = chosen.get("maxContext", 128000)
            if ctx_tokens > max_ctx:
                print(f"[prune] Warning: context ({ctx_tokens:,} tokens) exceeds {chosen['label']} limit ({max_ctx:,}). Response may be truncated.")

        # ── Build messages ────────────────────────────────────────────
        system_parts = [
            "You are a coding assistant with deep knowledge of the user's codebase.",
            "Answer concisely and directly. Refer to specific files and line numbers when relevant.",
        ]
        # Layer 1: full project context loaded once at session start
        if project_context:
            system_parts.append(
                f"\n## Project Overview (loaded once for this session)\n{project_context}"
            )
        # Layer 2: per-prompt pruned snippets — only the relevant code for this question
        if ctx_text:
            system_parts.append(
                f"\n## Relevant Code for This Question (pruned by Scout)\n{ctx_text}"
            )

        messages = [{"role": "system", "content": "\n".join(system_parts)}]
        messages += history
        messages.append({"role": "user", "content": prompt})

        # ── Stream response ───────────────────────────────────────────
        print(f"\n{chosen['label']}> ", end="", flush=True)
        tok_in, tok_out = _stream_response(chosen, messages, env, stats)

        # Keep history (last 10 turns to avoid ballooning)
        history.append({"role": "user",      "content": prompt})
        history.append({"role": "assistant", "content": "[see above]"})
        if len(history) > 20:
            history = history[-20:]

        if tok_out > 0:
            print(f"  [{chosen['label']} | +{tok_in+tok_out:,} tokens today]\n")


# ── Entry point ───────────────────────────────────────────────────────
def main():
    args = sys.argv[1:]

    if not args or args[0] in ("--help", "-h"):
        print("""
PruneTool CLI

  prune chat              Start interactive chat (auto-routes models)
  prune model <alias>     Lock to a model  (sonnet / opus / haiku / groq / gemini / gpt-4o / auto)
  prune models            List all models, keys, and daily usage
  prune status            Show gateway + active model status

Inside chat:
  /describe               Load project context into this session
  /model <alias>          Switch model mid-session
  /models                 Show model list
  /status                 Show status
  /clear                  Clear conversation history
  /quit                   Exit
""")
        return

    env    = _load_env()
    config = _load_llm_config(env)
    stats  = DailyStats()

    cmd = args[0].lower()

    if cmd == "chat":
        cmd_chat(config, env, stats)

    elif cmd == "model":
        if len(args) < 2:
            print("Usage: prune model <alias>  (sonnet / haiku / groq / gemini / gpt-4o / auto)")
            sys.exit(1)
        cmd_model(args[1], config, env)

    elif cmd == "models":
        cmd_models(config, env, stats)

    elif cmd == "status":
        cmd_status(config, env)

    else:
        print(f"[prune] Unknown command '{cmd}'. Run 'prune --help' for usage.")
        sys.exit(1)


if __name__ == "__main__":
    main()
