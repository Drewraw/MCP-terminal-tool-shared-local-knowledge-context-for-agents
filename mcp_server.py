"""
mcp_server.py — MCP Specialist Interface for PruneTool
=======================================================
FastAPI-based Model Context Protocol server.  Acts as middleware between
external LLMs (Claude, GPT, Gemini …) and the target project's metadata.

Every tool call and response is printed to the terminal so the user always
sees the full LLM ↔ MCP conversation in real time.

MCP Tools:
  describe_project     — capabilities + health report (what exists / missing)
  analyze_complexity   — model recommendation based on query complexity
  report_tokens        — LLM reports token usage; alerts at 35k/session
  save_docs            — LLM writes session summary to prune library/

Side-cars:
  Watchdog      — monitors prune library/ → SHA-256 verify → gateway re-scan
  Token Monitor — reads token_log.jsonl; terminal alert if > 35 K tokens / session

Run:
  PRUNE_CODEBASE_ROOT=/your/project python mcp_server.py
  Defaults to cwd if env var not set (same behaviour as gateway.py).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import sys
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

# ── Watchdog ─────────────────────────────────────────────────────────
try:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer
    WATCHDOG_AVAILABLE = True
except ImportError:
    WATCHDOG_AVAILABLE = False

# ════════════════════════════════════════════════════════════════════
# CONFIG & PATHS
# ════════════════════════════════════════════════════════════════════

ROOT          = Path(__file__).resolve().parent          # prunetool install
CODEBASE_ROOT = Path(os.environ.get("PRUNE_CODEBASE_ROOT", os.getcwd()))

PRUNETOOL_DATA = CODEBASE_ROOT / ".prunetool"
PRUNE_LIBRARY  = CODEBASE_ROOT / "prune library"
TOKEN_LOG      = CODEBASE_ROOT / "token_log.jsonl"
SESSION_LOG    = CODEBASE_ROOT / "session_log.jsonl"

# llms_prunetoolfinder.js — user's model suggestion config in their project
USER_FINDER_JS = CODEBASE_ROOT / "llms_prunetoolfinder.js"

GATEWAY_URL           = os.environ.get("GATEWAY_URL", "http://localhost:8000")
TOKEN_ALERT_THRESHOLD = 20_000
TOKEN_POLL_SECONDS    = 30

# ── Instruction Fade ─────────────────────────────────────────────────
# How many tokens into a session before we worry the LLM has forgotten
# its initial project context (describe_project output fades in long sessions).
FADE_CHECK_INTERVAL   = 15_000   # check every 15k tokens
FADE_AUTO_THRESHOLD   = 40_000   # at this point, auto-refresh without asking
FADE_ASK_THRESHOLD    = 20_000   # at this point, ask user first

# Per-model: tokens accumulated since last context refresh
# { model_id: int }
_tokens_since_refresh: Dict[str, int] = defaultdict(int)

# Per-model: how many fades have happened (so message escalates)
_fade_count: Dict[str, int] = defaultdict(int)

# Per-model: whether we're currently waiting for user answer on fade prompt
_fade_pending_ask: Dict[str, bool] = defaultdict(bool)

# ════════════════════════════════════════════════════════════════════
# SSE CLIENT REGISTRY  — connected LLM clients listening on /sse
# ════════════════════════════════════════════════════════════════════

# Each entry is an asyncio.Queue — one per connected LLM client
_sse_clients: List[asyncio.Queue] = []

# Snapshot of doc hashes+line counts — compared on every watchdog trigger
# { absolute_path_str: {"hash": str, "lines": int} }
_doc_snapshot: Dict[str, dict] = {}


def _snapshot_docs() -> Dict[str, dict]:
    """
    Compute SHA-256 + line count for every file in prune library/ and README.md.
    Returns a fresh snapshot dict.
    """
    snap: Dict[str, dict] = {}
    # prune library docs
    if PRUNE_LIBRARY.exists():
        for f in sorted(PRUNE_LIBRARY.glob("*.md")):
            try:
                raw = f.read_bytes()
                snap[str(f)] = {
                    "hash":  hashlib.sha256(raw).hexdigest(),
                    "lines": raw.decode("utf-8", errors="replace").count("\n"),
                    "name":  f.name,
                }
            except OSError:
                pass
    # README.md in project root
    for name in ("README.md", "readme.md"):
        readme = CODEBASE_ROOT / name
        if readme.exists():
            try:
                raw = readme.read_bytes()
                snap[str(readme)] = {
                    "hash":  hashlib.sha256(raw).hexdigest(),
                    "lines": raw.decode("utf-8", errors="replace").count("\n"),
                    "name":  readme.name,
                }
            except OSError:
                pass
            break
    return snap


def _diff_snapshots(before: Dict[str, dict], after: Dict[str, dict]) -> List[str]:
    """
    Compare two snapshots and return human-readable change lines.
    """
    msgs: List[str] = []
    all_keys = set(before) | set(after)
    for k in sorted(all_keys):
        name = after.get(k, before.get(k, {})).get("name", Path(k).name)
        if k not in before:
            msgs.append(f"  + {name}  (new file)")
        elif k not in after:
            msgs.append(f"  - {name}  (deleted)")
        elif before[k]["hash"] != after[k]["hash"]:
            dl = after[k]["lines"] - before[k]["lines"]
            sign = f"+{dl}" if dl >= 0 else str(dl)
            msgs.append(f"  ~ {name}  ({sign} lines)")
    return msgs


async def _push_notification(level: str, message: str):
    """
    Push a JSON-RPC notification to ALL connected LLM clients via SSE.
    Also prints to terminal as fallback.
    """
    payload = json.dumps({
        "jsonrpc": "2.0",
        "method":  "notifications/message",
        "params": {
            "level":  level,           # "info" | "warning" | "error"
            "logger": "prunetool-mcp",
            "data":   message,
        },
    })
    # Terminal fallback — always visible
    ts = time.strftime("%H:%M:%S")
    print(f"\n  [NOTIFY {ts}] {message}\n", flush=True)
    # Push to every connected SSE client
    dead = []
    for q in _sse_clients:
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            dead.append(q)
    for q in dead:
        _sse_clients.remove(q)


STOPWORDS = {
    "a","an","the","is","in","on","at","to","of","and","or","for","with",
    "by","from","how","what","where","why","does","do","can","will","would",
    "should","this","that","it","be","are","was","were","has","have","had","not",
}

# ════════════════════════════════════════════════════════════════════
# AUTO-SETUP  — create everything the project needs on first run
# ════════════════════════════════════════════════════════════════════

# Empty JSON templates written when files don't exist yet.
# Gateway /re-scan will overwrite these with real data.
_JSON_TEMPLATES: Dict[str, Any] = {
    "skeleton.json": {
        "root_path": "",          # filled by gateway
        "entries":   [],
    },
    "folder_map.json": {
        "folders": {},
        "edges":   [],
        "stats":   {"total_folders": 0, "total_edges": 0, "total_files": 0},
    },
    "auto_annotations.json": {},
    "project_metadata.json": {
        "root_path":          "",
        "readme_overview":    "",
        "readme_path":        "",
        "file_count":         0,
        "total_symbols":      0,
        "language_breakdown": {},
        "directory_tree":     {},
        "last_scanned_at":    None,
    },
    "readme_context.json": {
        "overview":    "",
        "readme_path": "",
        "char_count":  0,
    },
}

# Starter content for prune library docs
_LIBRARY_MD = """\
# Project Library

> Long-term memory for this project. Updated by the LLM when user runs /save docs.

## Architecture


## Key Decisions


## Current Status


## Next Steps

"""

_PROGRESS_MD = """\
# Progress Log

> Append a dated entry here each time the user runs /save docs.

## Current Status
- [ ] Gateway scanned project
- [ ] First session logged

---
"""

def auto_setup() -> List[str]:
    """
    Auto-create all folders and files PruneTool needs in the target project.
    Returns a list of human-readable messages describing what was created.
    Safe to call on every startup — skips items that already exist.
    """
    created = []

    # ── 1. .prunetool/ folder + all JSON files ───────────────────────
    if not PRUNETOOL_DATA.exists():
        PRUNETOOL_DATA.mkdir(parents=True, exist_ok=True)
        created.append(f"Created  .prunetool/  (Knowledge Graph folder)")

    for filename, template in _JSON_TEMPLATES.items():
        fpath = PRUNETOOL_DATA / filename
        if not fpath.exists():
            # Stamp root_path where the template has it
            if isinstance(template, dict) and "root_path" in template:
                template = {**template, "root_path": str(CODEBASE_ROOT)}
            fpath.write_text(
                json.dumps(template, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            created.append(f"Created  .prunetool/{filename}  (empty template)")

    # ── 2. prune library/ folder + starter docs ──────────────────────
    if not PRUNE_LIBRARY.exists():
        PRUNE_LIBRARY.mkdir(parents=True, exist_ok=True)
        created.append(f"Created  prune library/  (long-term memory folder)")

    lib_md = PRUNE_LIBRARY / "library.md"
    if not lib_md.exists():
        lib_md.write_text(_LIBRARY_MD, encoding="utf-8")
        created.append(f"Created  prune library/library.md  (starter doc)")

    prog_md = PRUNE_LIBRARY / "PROGRESS.md"
    if not prog_md.exists():
        prog_md.write_text(_PROGRESS_MD, encoding="utf-8")
        created.append(f"Created  prune library/PROGRESS.md  (starter doc)")

    # ── 3. llms_prunetoolfinder.js — copied from install template ───────
    # Only copied if it doesn't exist yet — user edits it to configure their models
    if not USER_FINDER_JS.exists():
        import shutil
        shutil.copy2(ROOT / "llms_prunetoolfinder.js", USER_FINDER_JS)
        created.append(
            f"Created  llms_prunetoolfinder.js  (open and uncomment your models)"
        )

    return created


def run_setup_check() -> tuple:
    """
    After auto_setup(), check what still needs human action.
    Returns (warnings list, models_info dict).
    """
    warnings = []

    sk = PRUNETOOL_DATA / "skeleton.json"
    if sk.exists():
        try:
            data = json.loads(sk.read_text(encoding="utf-8"))
            if not data.get("entries"):
                warnings.append({
                    "id":    "skeleton_empty",
                    "label": ".prunetool/skeleton.json is empty",
                    "why":   "No symbols indexed yet — Knowledge Graph will be empty.",
                    "fix":   "Start the gateway and trigger a scan:",
                    "example": (
                        "# Terminal 1:\n"
                        f"  PRUNE_CODEBASE_ROOT=\"{CODEBASE_ROOT}\" "
                        f"python \"{ROOT / 'server' / 'gateway.py'}\"\n"
                        "# Terminal 2:\n"
                        "  curl.exe -X POST http://localhost:8000/re-scan"
                    ),
                })
        except Exception:
            pass

    # ── Check llms_prunetoolfinder.js ────────────────────────────────
    models_info = {"status": "missing", "models": []}
    if USER_FINDER_JS.exists():
        try:
            data   = _parse_llm_finder(USER_FINDER_JS)
            models = data.get("models", [])
            if models:
                models_info = {"status": "configured", "models": models}
            else:
                models_info = {"status": "empty", "models": []}
        except Exception:
            models_info = {"status": "parse_error", "models": []}
    else:
        models_info = {"status": "missing", "models": []}

    return warnings, models_info


def print_setup_report(created: List[str], warnings: List[dict], models_info: dict):
    """Print auto-setup results and any remaining action items."""
    if created:
        print(_box_row(f"  Auto-setup: {len(created)} item(s) created"), flush=True)
        for msg in created:
            print(_box_row(f"  + {msg}"), flush=True)
        print(_box_sep(), flush=True)

    if not warnings:
        print(_box_row("  All checks passed. Ready."), flush=True)
    else:
        print(_box_row(f"  Action needed — {len(warnings)} item(s):"), flush=True)
        for i, w in enumerate(warnings, 1):
            print(_box_sep(), flush=True)
            print(_box_row(f"  [{i}] {w['label']}"), flush=True)
            print(_box_row(f"      Why : {w['why']}"), flush=True)
            print(_box_row(f"      Fix : {w['fix']}"), flush=True)
            for line in w["example"].splitlines():
                print(_box_row(f"           {line}"), flush=True)
        print(_box_sep(), flush=True)
        print(_box_row("  MCP is running — fix above to unlock all tools."), flush=True)

    # ── llms_prunetoolfinder.js status ───────────────────────────────
    print(_box_sep(), flush=True)
    status = models_info.get("status", "missing")
    models = models_info.get("models", [])

    if status == "configured":
        print(_box_row(f"  Models ({len(models)} configured in llms_prunetoolfinder.js):"), flush=True)
        for m in models:
            label     = m.get("label", m.get("model", "?"))
            model_id  = m.get("model", "?")
            complexity = m.get("complexity", "?")
            print(_box_row(f"    + {label}  [{model_id}]  ({complexity})"), flush=True)
    elif status == "empty":
        print(_box_row("  llms_prunetoolfinder.js — no models configured"), flush=True)
        print(_box_row(f"  Open {USER_FINDER_JS}"), flush=True)
        print(_box_row("  and uncomment the models you have access to."), flush=True)
    elif status == "missing":
        print(_box_row("  llms_prunetoolfinder.js — file not found"), flush=True)
        print(_box_row("  It will be created on next startup."), flush=True)
    else:
        print(_box_row("  llms_prunetoolfinder.js — could not parse (check syntax)"), flush=True)

    print(_box_sep(), flush=True)
    print(_box_row("  Tip: Call 'describe_project' manually when you need KB context."), flush=True)
    print(_box_row("  When done, say '/save docs' — LLM updates prune library/"), flush=True)

# ════════════════════════════════════════════════════════════════════
# TERMINAL CONVERSATION LOGGER
# ════════════════════════════════════════════════════════════════════

# Force UTF-8 output on Windows so box-drawing chars don't crash
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("mcp")

_W = 62   # terminal box width

def _box_top(label: str) -> str:
    inner = f"  {label}  "
    pad = max(0, _W - len(inner) - 2)
    return f"\n+{inner}{'-' * pad}+"

def _box_row(text: str) -> str:
    lines = []
    for chunk in (text if isinstance(text, list) else [text]):
        for line in str(chunk).splitlines():
            while len(line) > _W - 4:
                lines.append(f"|  {line[:_W-4]}  |")
                line = "   " + line[_W-4:]
            lines.append(f"|  {line:<{_W-4}}  |")
    return "\n".join(lines)

def _box_sep() -> str:
    return f"|{'─' * (_W)}|"

def _box_bot() -> str:
    return f"+{'─' * _W}+"


def log_call(tool: str, args: dict):
    """Print incoming LLM → MCP tool call to the terminal."""
    ts = time.strftime("%H:%M:%S")
    args_str = json.dumps(args, ensure_ascii=False)
    if len(args_str) > 120:
        args_str = args_str[:117] + "..."
    print(_box_top(f"LLM -> MCP  [{ts}]"), flush=True)
    print(_box_row(f"Tool : {tool}"), flush=True)
    print(_box_row(f"Args : {args_str}"), flush=True)
    print(_box_bot(), flush=True)


def log_response(tool: str, result: dict, elapsed_s: float):
    """Print MCP → LLM response summary to the terminal."""
    ts = time.strftime("%H:%M:%S")
    print(_box_top(f"MCP -> LLM  [{ts}]  +{elapsed_s:.2f}s"), flush=True)
    print(_box_row(f"Tool : {tool}"), flush=True)
    print(_box_sep(), flush=True)

    # Tool-specific summary lines
    if tool == "describe_project":
        print(_box_row(f"Status     : {result.get('status','?')}"), flush=True)
        ks = result.get("knowledge_summary", {})
        for fact in ks.get("what_i_know", []):
            print(_box_row(f"  ✓  {fact}"), flush=True)
        caps = result.get("capabilities", [])
        for c in caps:
            print(_box_row(f"  OK  {c}"), flush=True)
        for issue in result.get("setup_issues", []):
            print(_box_sep(), flush=True)
            print(_box_row(f"  MISSING: {issue['missing']}"), flush=True)
            print(_box_row(f"  Why    : {issue['why']}"), flush=True)
            print(_box_row(f"  Fix    : {issue['fix']}"), flush=True)
            for ex_line in issue["example"].splitlines():
                print(_box_row(f"  Ex     : {ex_line}"), flush=True)

        print(_box_row(f"Symbols    : {result.get('symbol_count',0)}"), flush=True)
        edges = result.get("cross_folder_edges", [])
        if edges:
            print(_box_row(f"Edges      : {len(edges)} cross-folder"), flush=True)

    elif tool == "analyze_complexity":
        st = result.get("stats", {})
        print(_box_row(f"Symbols    : {st.get('total_symbols','?')}  Files: {st.get('total_files','?')}  Edges: {st.get('cross_folder_edges','?')}"), flush=True)
        tier = result.get("complexity_tier", "?")
        sug  = result.get("suggested_model", {})
        if sug:
            print(_box_row(f"Tier       : {tier.upper()} → {sug.get('label','')} ({sug.get('model','')})"), flush=True)
        else:
            print(_box_row(f"Tier       : {tier.upper()} (no model configured)"), flush=True)
        print(_box_row(f"Reason     : {result.get('rationale','')[:80]}"), flush=True)

    else:
        summary = str(result)[:200]
        print(_box_row(summary), flush=True)

    print(_box_bot(), flush=True)


def log_alert(message: str):
    """Print a token-threshold alert to the terminal."""
    ts = time.strftime("%H:%M:%S")
    print(_box_top(f"!! TOKEN ALERT  [{ts}] !!"), flush=True)
    print(_box_row(message), flush=True)
    print(_box_row("Action: run /save docs to write context to prune library/"), flush=True)
    print(_box_bot(), flush=True)


def log_watchdog(message: str):
    try:
        if sys.is_finalizing():
            return
        ts = time.strftime("%H:%M:%S")
        print(f"  [watchdog {ts}] {message}", flush=True)
    except Exception:
        pass


# ════════════════════════════════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════════════════════════════════

def _load_json(path: Path) -> Optional[Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _parse_llm_finder(path: Path) -> Dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    # Strip // line comments — but NOT https:// or similar URLs inside strings
    text = re.sub(r'(?<!:)(?<!\\)//[^\n]*', "", text)
    # Strip /* block */ comments
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    # Remove module.exports = wrapper
    text = re.sub(r"^\s*module\.exports\s*=\s*", "", text.strip())
    text = text.rstrip(";").strip()
    # Remove trailing commas before } or ]
    text = re.sub(r",\s*([}\]])", r"\1", text)
    # Quote unquoted JS object keys  e.g.  scout: [ → "scout": [
    text = re.sub(r'(?<=[{,])\s*(\w+)\s*:', lambda m: f' "{m.group(1)}":', text)
    # Remove stray control characters that break JSON parsing
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text)
    return json.loads(text)


# ════════════════════════════════════════════════════════════════════
# MCP TOOLS
# ════════════════════════════════════════════════════════════════════

# ── Tool 0: describe_project ─────────────────────────────────────────

def _parse_knowledge_summary(content: str) -> dict:
    """
    Parse terminal_context.md and return a structured summary of what
    the LLM now knows — folder count, files, symbols, import edges,
    library docs, README presence.
    """
    import re

    summary = {
        "what_i_know": [],
        "folders":      [],
        "import_edges": [],
        "library_docs": [],
        "has_readme":   False,
    }

    lines = content.splitlines()
    current_section = None

    for line in lines:
        # Detect section headers
        if line.startswith("## Knowledge Graph"):
            current_section = "kg"
        elif line.startswith("## ") and "prune library" in line.lower():
            current_section = "lib"
            # Extract doc name: ## PROGRESS  [prune library/PROGRESS.md]
            m = re.search(r'\[prune library/([^\]]+)\]', line)
            if m:
                summary["library_docs"].append(m.group(1))
        elif line.startswith("## README"):
            current_section = "readme"
            summary["has_readme"] = True
        elif line.startswith("## "):
            current_section = None

        if current_section == "kg":
            # Files indexed
            m = re.search(r'Files indexed:\s*(\d+)', line)
            if m:
                summary["files_indexed"] = int(m.group(1))
            # Symbols indexed
            m = re.search(r'Symbols indexed:\s*(\d+)', line)
            if m:
                summary["symbols_indexed"] = int(m.group(1))
            # Folder line: "  - foldername  (N files)"
            m = re.match(r'\s+-\s+(\S+)\s+\((\d+) files\)', line)
            if m:
                summary["folders"].append({"folder": m.group(1), "files": int(m.group(2))})
            elif re.match(r'\s+-\s+(\S+)\s*$', line) and current_section == "kg":
                m2 = re.match(r'\s+-\s+(\S+)\s*$', line)
                if m2 and not m2.group(1).startswith("Not"):
                    summary["folders"].append({"folder": m2.group(1), "files": 0})
            # Import edge: "  - A → B  (weight: N)"
            m = re.search(r'-\s+(.+?)\s+→\s+(.+?)\s+\(weight', line)
            if m:
                summary["import_edges"].append(f"{m.group(1).strip()} → {m.group(2).strip()}")

    # Build human-readable "what_i_know" list
    what = []
    fi = summary.get("files_indexed")
    si = summary.get("symbols_indexed")
    if fi:
        what.append(f"Codebase index: {fi} files, {si or '?'} symbols")
    if summary["folders"]:
        what.append(f"Folder map: {len(summary['folders'])} folders known")
    if summary["import_edges"]:
        what.append(f"Import graph: {len(summary['import_edges'])} cross-folder dependency edges")
    if summary["library_docs"]:
        what.append(f"Prune library: {len(summary['library_docs'])} doc(s) — {', '.join(summary['library_docs'])}")
    if summary["has_readme"]:
        what.append("README.md: available (project overview loaded)")

    summary["what_i_know"] = what
    return summary


def _build_fade_refresh_context() -> str:
    """
    Build a compact (~600 token) context reminder for instruction fade refresh.
    Contains only the high-signal parts: folder map summary, key annotations,
    prune library headlines. NOT the full 5500-token context.
    """
    parts = []

    # 1. Prune library headlines (most important — user's own notes)
    lib_path = CODEBASE_ROOT / "prune library"
    if lib_path.exists():
        headlines = []
        for md in sorted(lib_path.glob("*.md"))[:3]:
            try:
                text = md.read_text(encoding="utf-8", errors="ignore")
                # First heading + first sentence under it only
                for line in text.splitlines():
                    line = line.strip()
                    if line.startswith("#"):
                        headlines.append(line)
                    elif line and not line.startswith("#") and headlines:
                        headlines.append("  " + line[:120])
                        break
            except OSError:
                pass
        if headlines:
            parts.append("## Project Notes (prune library)\n" + "\n".join(headlines[:10]))

    # 2. Folder map summary — top folders and their roles
    fm_path = PRUNETOOL_DATA / "folder_map.json"
    if fm_path.exists():
        try:
            fm = json.loads(fm_path.read_text(encoding="utf-8"))
            folders = fm.get("folders", {})
            stats   = fm.get("stats", {})
            lines   = [f"## Folder Map ({stats.get('total_folders',0)} folders, {stats.get('total_edges',0)} edges)"]
            # Top 8 folders by file count
            top = sorted(folders.items(), key=lambda x: x[1].get("file_count", 0), reverse=True)[:8]
            for name, data in top:
                ann  = data.get("annotation", "")
                fc   = data.get("file_count", 0)
                imp  = ", ".join(data.get("imports_from", [])[:3])
                note = f" — {ann}" if ann else (f" → imports {imp}" if imp else "")
                lines.append(f"  {name} ({fc} files){note}")
            parts.append("\n".join(lines))
        except Exception:
            pass

    # 3. Auto-annotations sample — first 10 file descriptions
    aa_path = PRUNETOOL_DATA / "auto_annotations.json"
    if aa_path.exists():
        try:
            aa = json.loads(aa_path.read_text(encoding="utf-8"))
            annots = aa.get("annotations", aa) if isinstance(aa, dict) else {}
            lines  = ["## Key File Summaries"]
            for fp, desc in list(annots.items())[:10]:
                lines.append(f"  {fp}: {str(desc)[:100]}")
            if lines:
                parts.append("\n".join(lines))
        except Exception:
            pass

    if not parts:
        return ""

    header = (
        "## ⟳ Context Refresh (instruction fade guard)\n"
        "Your session is long. Here is a compact reminder of the project context.\n"
    )
    return header + "\n\n".join(parts)


async def _fetch_context_version() -> dict:
    """Fetch current context version from gateway."""
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(f"{GATEWAY_URL}/context-version")
            if r.status_code == 200:
                return r.json()
    except Exception:
        pass
    return {"version": "", "sections": {}}


def _extract_section(content: str, heading: str) -> str:
    """Extract a ## section block from terminal_context.md."""
    pattern = rf"(^|\n)(## {re.escape(heading)}.*?)(?=\n## |\Z)"
    m = re.search(pattern, content, re.DOTALL)
    return m.group(2).strip() if m else ""


async def describe_project(arguments: dict) -> dict:
    """
    Returns project context for the LLM.
    Supports delta mode: pass since_version to get only what changed.
    - First call (no since_version): full context ~5500 tokens
    - since_version matches current: {"status":"up_to_date"} ~30 tokens
    - since_version differs: only changed sections ~300 tokens
    """
    model         = (arguments.get("model")         or "unknown").strip()
    since_version = (arguments.get("since_version") or "").strip()

    if model != "unknown":
        print(f"[describe_project] LLM connected: {model}", flush=True)

    terminal_ctx = PRUNETOOL_DATA / "terminal_context.md"

    if not terminal_ctx.exists():
        return {
            "status": "not_ready",
            "error":  "terminal_context.md not found — run a project scan first (gateway must be running on port 8000).",
            "project_root": str(CODEBASE_ROOT),
        }

    # Fetch current version from gateway
    version_info  = await _fetch_context_version()
    current_ver   = version_info.get("version", "")
    cur_sections  = version_info.get("sections", {})

    # ── Case 1: LLM already has the latest version ───────────────────
    if since_version and since_version == current_ver:
        print(f"[describe_project] {model} context up-to-date (v{current_ver}) — 30 tokens", flush=True)
        return {
            "status":          "up_to_date",
            "version":         current_ver,
            "message":         "Your project context is current. No re-read needed.",
        }

    raw_content = terminal_ctx.read_text(encoding="utf-8")
    content = re.sub(r'\n_\(updated [^)]+\)_\n?', '\n', raw_content)
    content = re.sub(r'\n_Last updated:.*?_\n?', '\n', content)
    content = re.sub(r'\n_Saved:.*?_\n?', '\n', content)
    content = re.sub(r'\n_Written:.*?_\n?', '\n', content)

    setup_issues, _ = run_setup_check()

    # ── Case 2: LLM has an older version — return delta only ─────────
    if since_version and since_version != current_ver and current_ver:
        # Determine which sections changed by comparing hashes
        # We don't store old section hashes per-LLM, so we return all
        # sections that have non-empty content as a compact delta
        section_names = ["Folder Map", "Auto Annotations", "Prune Library", "README"]
        delta_parts   = []
        for sec in section_names:
            block = _extract_section(content, sec)
            if block:
                delta_parts.append(block)

        delta_context = "\n\n".join(delta_parts)
        knowledge_summary = _parse_knowledge_summary(content)

        print(
            f"[describe_project] {model} delta update "
            f"v{since_version} → v{current_ver} (~{len(delta_context)//4} tokens)",
            flush=True
        )
        print(
            f"┌─ Context delta sent to {model} ───────────────────────────────────",
            flush=True
        )
        print(f"│  Old version : {since_version}", flush=True)
        print(f"│  New version : {current_ver}", flush=True)
        print(f"│  Tokens sent : ~{len(delta_context)//4} (was ~5500 full)", flush=True)
        print(f"└──────────────────────────────────────────────────────────────────", flush=True)

        return {
            "status":            "updated",
            "version":           current_ver,
            "previous_version":  since_version,
            "delta_context":     delta_context,
            "knowledge_summary": knowledge_summary,
            "message":           f"Context updated from v{since_version} to v{current_ver}. Apply the delta sections above.",
            "compiled_at":       time.strftime("%Y-%m-%d %H:%M:%S"),
        }

    # ── Case 3: First call — return full context ──────────────────────
    knowledge_summary = _parse_knowledge_summary(content)

    print(
        f"[describe_project] {model} full context sent "
        f"(v{current_ver}, ~{len(content)//4} tokens)",
        flush=True
    )
    print(
        f"┌─ Full context sent to {model} ────────────────────────────────────",
        flush=True
    )
    print(f"│  Version  : {current_ver}", flush=True)
    print(f"│  Tokens   : ~{len(content)//4}", flush=True)
    print(f"│  Tip      : Pass since_version='{current_ver}' next call for delta", flush=True)
    print(f"└──────────────────────────────────────────────────────────────────", flush=True)

    return {
        "status":            "ready",
        "version":           current_ver,
        "project_root":      str(CODEBASE_ROOT),
        "setup_required":    len(setup_issues) > 0,
        "setup_issues": [
            {
                "id":      issue["id"],
                "missing": issue["label"],
                "why":     issue["why"],
                "fix":     issue["fix"],
                "example": issue["example"],
            }
            for issue in setup_issues
        ],
        "knowledge_summary": knowledge_summary,
        "context":           content,
        "handoff_suggestion": _handoff_suggestion(model),
        "compiled_at":       time.strftime("%Y-%m-%d %H:%M:%S"),
    }


# ── Tool: report_tokens ──────────────────────────────────────────────

async def report_tokens(arguments: dict) -> dict:
    """
    Called by the external LLM after every response to report
    how many tokens it consumed. MCP accumulates the total and
    fires a /save docs alert when the session hits 35k.
    """
    global _session_tokens, _session_start

    input_tokens        = int(arguments.get("input_tokens",         0))
    output_tokens       = int(arguments.get("output_tokens",        0))
    cached_input_tokens = int(arguments.get("cached_input_tokens",  0))
    # Fallback: if caller still sends only "tokens", treat as total
    total_fallback = int(arguments.get("tokens", 0))
    if input_tokens == 0 and output_tokens == 0 and total_fallback > 0:
        input_tokens  = total_fallback
        output_tokens = 0
    raw_count   = input_tokens + output_tokens
    model       = (arguments.get("model") or "unknown").strip()
    user_msg    = (arguments.get("user_message") or "").strip().lower()
    if raw_count <= 0:
        return {"error": "input_tokens and output_tokens must sum to a positive integer"}

    # Exit keyword detection — check before anything else
    EXIT_KEYWORDS = {"exit", "quit", "bye", "goodbye", "done", "finished",
                     "cya", "see you", "talk later", "closing", "shutting down"}
    if any(kw in user_msg for kw in EXIT_KEYWORDS):
        print(f"[exit_guard] Exit keyword detected in user message for {model}", flush=True)
        return {
            "exit_warning":  True,
            "SHOW_NOW":      True,
            "user_message":  "As you are exiting terminal, please make sure to save your work by giving command to LLMs 'save docs' for safer side.",
            "instruction":   "STOP. Print the user_message above verbatim. Ask: 'Do you still want to exit without saving?' — if yes, call session_end then say goodbye. If no, run save docs first.",
        }

    # Effective cost: cached input tokens are billed at 0.1x, so subtract 90% of their cost
    effective_tokens = raw_count - int(cached_input_tokens * 0.9)

    _session_tokens += effective_tokens
    _tokens_since_refresh[model] += effective_tokens
    log.debug("[token_monitor] +%d effective tokens (%s) → session total: %d", effective_tokens, model, _session_tokens)

    # Write to token_log.jsonl so the dashboard picks it up
    try:
        entry = json.dumps({
            "ts":                   time.time(),
            "tokens":               raw_count,
            "effective_tokens":     effective_tokens,
            "input_tokens":         input_tokens,
            "output_tokens":        output_tokens,
            "cached_input_tokens":  cached_input_tokens,
            "model":                model,
            "query":                "mcp:report_tokens",
        })
        with open(TOKEN_LOG, "a", encoding="utf-8") as f:
            f.write(entry + "\n")
    except OSError:
        pass

    # Accumulate effective cost in-memory (what counts against daily goal)
    _rt_model_burned[model] = _rt_model_burned.get(model, 0) + effective_tokens

    # Real-time goal check — no polling delay, uses in-memory total
    try:
        asyncio.get_event_loop().create_task(_check_model_daily_goal(model))
    except Exception as e:
        print(f"[rt_goal] Error scheduling goal check: {e}", flush=True)

    # Per-model threshold from llms_prunetoolfinder.js (falls back to TOKEN_ALERT_THRESHOLD)
    _model_threshold = TOKEN_ALERT_THRESHOLD
    try:
        _user_models = _load_user_models()
        _model_entry = next((m for m in _user_models if m.get("model") == model), None)
        if _model_entry:
            _goal = int(_model_entry.get("dailyTokenGoal") or 0)
            if _goal:
                _model_threshold = _goal
    except Exception:
        pass

    if _session_tokens >= 100_000:
        used = _session_tokens
        msg = (
            f"Token threshold reached this session ({used:,} tokens used / {_model_threshold:,} limit for {model})."
        )
        log_alert(msg)
        await _push_notification("warning", msg)
        # Reset for next block
        _session_tokens = 0
        _session_start  = time.time()

        return {
            "session_tokens": used,
            "threshold":      _model_threshold,
            "remaining":      _model_threshold,
            "SAVE_NOW":       True,
            "user_message": (
                "\n"
                "⚠️  Token threshold reached — context may be lost soon.\n\n"
                "Please do two things:\n"
                "  1. Type 'save docs' — I'll save everything we've done this session to the prune library\n"
                "  2. Click 'Project Scan' in the PruneTool dashboard — this updates the context for the next session\n\n"
                "I'll wait for you to confirm before continuing."
            ),
            "instruction": "STOP. Print the user_message field above verbatim to the user. Do not paraphrase it.",
        }

    # ── 30-min burn rate handoff check ──────────────────────────────────
    if model and model not in _rt_alerted_30min_handoff:
        try:
            burned_30min = _tokens_last_30min(model)
            if burned_30min >= 100_000:
                user_models  = _load_user_models()
                calling_entry = next((m for m in user_models if m.get("model") == model), None)
                if calling_entry and calling_entry.get("complexity") == "complex":
                    medium = _suggest_model("medium", user_models)
                    if medium and medium["model"] != model:
                        _rt_alerted_30min_handoff.add(model)
                        return {
                            "session_tokens": _session_tokens,
                            "burn_rate_warning": True,
                            "user_message": (
                                f"⚠️  High token burn detected: {burned_30min:,} tokens in the last 30 minutes.\n\n"
                                f"Consider switching to a medium complexity model from your PruneTool config "
                                f"to reduce token spend for most coding tasks."
                            ),
                            "instruction": "Print the user_message above verbatim to the user. Do not paraphrase.",
                        }
        except Exception:
            pass

    burned = _rt_model_burned.get(model, _session_tokens)

    # ── Instruction Fade Guard ───────────────────────────────────────
    since_refresh = _tokens_since_refresh.get(model, 0)

    # Only check at FADE_CHECK_INTERVAL boundaries to avoid spam
    prev_bucket = (since_refresh - effective_tokens) // FADE_CHECK_INTERVAL
    curr_bucket = since_refresh // FADE_CHECK_INTERVAL
    fade_bucket_crossed = curr_bucket > prev_bucket and curr_bucket > 0

    if fade_bucket_crossed and not _fade_pending_ask.get(model):
        fade_no      = _fade_count[model] + 1
        refresh_ctx  = _build_fade_refresh_context()
        ctx_tokens   = len(refresh_ctx) // 4 if refresh_ctx else 0

        # ── Auto-refresh at high token count ────────────────────────
        if since_refresh >= FADE_AUTO_THRESHOLD:
            _fade_count[model]          += 1
            _tokens_since_refresh[model] = 0  # reset counter

            print(f"\n┌─ Instruction Fade Guard — Auto Refresh #{fade_no} ({model}) ────────────────", flush=True)
            print(f"│  Session tokens since last refresh : {since_refresh:,}", flush=True)
            print(f"│  Threshold                         : {FADE_AUTO_THRESHOLD:,} (auto mode)", flush=True)
            print(f"│  Compact context injected          : ~{ctx_tokens} tokens", flush=True)
            print(f"│  Full describe_project             : ~5,500 tokens (avoided)", flush=True)
            print(f"└────────────────────────────────────────────────────────────────────────────\n", flush=True)

            if refresh_ctx:
                return {
                    "ok":              True,
                    "fade_refresh":    True,
                    "fade_number":     fade_no,
                    "tokens_in_session": since_refresh,
                    "context_reminder": refresh_ctx,
                    "instruction": (
                        "IMPORTANT: Re-read the context_reminder field above. "
                        "Your session is long and earlier instructions may have faded. "
                        "Silently absorb this context — do not print it to the user. "
                        "Continue the conversation normally."
                    ),
                }

        # ── Ask user at lower threshold ──────────────────────────────
        elif since_refresh >= FADE_ASK_THRESHOLD:
            _fade_pending_ask[model] = True

            print(f"\n┌─ Instruction Fade Guard — Asking User #{fade_no} ({model}) ──────────────────", flush=True)
            print(f"│  Session tokens since last refresh : {since_refresh:,}", flush=True)
            print(f"│  Threshold                         : {FADE_ASK_THRESHOLD:,} (ask mode)", flush=True)
            print(f"│  Auto-refresh kicks in at          : {FADE_AUTO_THRESHOLD:,} tokens", flush=True)
            print(f"│  Compact refresh cost              : ~{ctx_tokens} tokens", flush=True)
            print(f"└────────────────────────────────────────────────────────────────────────────\n", flush=True)

            return {
                "ok":              True,
                "fade_check":      True,
                "fade_number":     fade_no,
                "tokens_in_session": since_refresh,
                "SHOW_NOW":        True,
                "user_message": (
                    f"\n🔄  Context Refresh Check — {since_refresh:,} tokens into this session\n\n"
                    f"Long sessions can cause earlier project context (folder structure, file roles) "
                    f"to fade from attention.\n\n"
                    f"Would you like a context refresh?\n"
                    f"  • Yes → I'll re-read key project context (~{ctx_tokens} tokens, not 5,500)\n"
                    f"  • No  → Continue as-is\n"
                    f"  • Auto → I'll refresh automatically every {FADE_AUTO_THRESHOLD:,} tokens from now\n"
                ),
                "instruction": (
                    "STOP. Print the user_message above verbatim. "
                    "Wait for the user to reply 'yes', 'no', or 'auto'. "
                    "If 'yes': call report_tokens with fade_response='yes' and your model. "
                    "If 'auto': call report_tokens with fade_response='auto' and your model. "
                    "If 'no': call report_tokens with fade_response='no' and your model."
                ),
            }

    # ── Handle user reply to fade ask ───────────────────────────────
    fade_response = (arguments.get("fade_response") or "").strip().lower()
    if fade_response and _fade_pending_ask.get(model):
        _fade_pending_ask[model] = False

        if fade_response == "no":
            _tokens_since_refresh[model] = 0  # reset, don't ask again until next interval
            return {"ok": True, "fade_skipped": True}

        if fade_response in ("yes", "auto"):
            if fade_response == "auto":
                # Lower the ask threshold so future checks go straight to auto
                # We signal this by bumping the counter high enough to hit FADE_AUTO_THRESHOLD
                _tokens_since_refresh[model] = FADE_AUTO_THRESHOLD

            refresh_ctx = _build_fade_refresh_context()
            ctx_tokens  = len(refresh_ctx) // 4 if refresh_ctx else 0
            fade_no     = _fade_count[model] + 1
            _fade_count[model]          += 1
            _tokens_since_refresh[model] = 0

            print(f"\n┌─ Instruction Fade Refresh #{fade_no} — User confirmed ({model}) ──────────────", flush=True)
            print(f"│  Compact context sent : ~{ctx_tokens} tokens", flush=True)
            print(f"└────────────────────────────────────────────────────────────────────────────\n", flush=True)

            return {
                "ok":              True,
                "fade_refresh":    True,
                "fade_number":     fade_no,
                "context_reminder": refresh_ctx,
                "instruction": (
                    "Re-read the context_reminder field above silently. "
                    "Then tell the user: 'Context refreshed — I've re-read the project structure.' "
                    "Continue the conversation normally."
                ),
            }

    # Silent response — no output to user unless threshold hit
    return {"ok": True}


# ── Session login / logout tracking ─────────────────────────────────

def _tokens_last_30min(model_id: str) -> int:
    """Sum effective_tokens for model_id from token_log.jsonl in the last 30 minutes."""
    cutoff = time.time() - 1800
    total  = 0
    try:
        if TOKEN_LOG.exists():
            for line in TOKEN_LOG.read_text(encoding="utf-8").splitlines():
                try:
                    e = json.loads(line.strip())
                    if float(e.get("ts", 0)) >= cutoff and e.get("model") == model_id:
                        total += int(e.get("effective_tokens") or e.get("tokens") or 0)
                except Exception:
                    pass
    except OSError:
        pass
    return total


def _seed_model_burned_from_log(model_id: str) -> int:
    """
    On login, read today's token_log.jsonl and sum all tokens for this model
    since midnight. Seeds _rt_model_burned so daily total is accurate across
    multiple login/logout cycles in the same day.
    """
    now      = time.time()
    midnight = now - (now % 86400)
    total    = 0
    try:
        if TOKEN_LOG.exists():
            for line in TOKEN_LOG.read_text(encoding="utf-8").splitlines():
                try:
                    e = json.loads(line.strip())
                    if float(e.get("ts", 0)) < midnight:
                        continue
                    if (e.get("model") or "unknown") == model_id:
                        # Use effective_tokens if present (cache-adjusted), else fall back to raw
                        total += int(e.get("effective_tokens") or e.get("tokens", 0))
                except Exception:
                    continue
    except OSError:
        pass
    return total


def _log_session_event(model_id: str, event: str, llm_timestamp: str = ""):
    """Write a login/logout event to session_log.jsonl."""
    try:
        entry = json.dumps({
            "ts":            time.time(),
            "model":         model_id,
            "event":         event,
            "server_time":   time.strftime("%Y-%m-%d %H:%M:%S"),
            "llm_time":      llm_timestamp or "",
        })
        with open(SESSION_LOG, "a", encoding="utf-8") as f:
            f.write(entry + "\n")
    except OSError:
        pass


def session_login(model_id: str, llm_timestamp: str = ""):
    """
    Called when an LLM connects. Seeds the daily token total from today's log
    so multiple sessions in the same day accumulate correctly.
    """
    today_burned = _seed_model_burned_from_log(model_id)
    _rt_model_burned[model_id] = today_burned
    _log_session_event(model_id, "login", llm_timestamp)
    ts_display = f" (LLM time: {llm_timestamp})" if llm_timestamp else ""
    print(f"\n[session] LOGIN  — {model_id}{ts_display} | {today_burned:,} tokens already burned today", flush=True)


def session_logout(model_id: str, llm_timestamp: str = ""):
    """Called when an LLM exits."""
    burned = _rt_model_burned.get(model_id, 0)
    _log_session_event(model_id, "logout", llm_timestamp)
    ts_display = f" (LLM time: {llm_timestamp})" if llm_timestamp else ""
    print(f"\n[session] LOGOUT — {model_id}{ts_display} | {burned:,} tokens burned today total", flush=True)


# ── Real-time per-model goal check (called from report_tokens) ───────

# Real-time in-memory token accumulator — seeded from token_log.jsonl on login
_rt_model_burned: dict = {}     # model_id → tokens burned today (cumulative across all sessions)
_rt_alerted_45: set = set()
_rt_alerted_70: set = set()
_rt_alerted_80: set = set()
_rt_alerted_90: set = set()
_rt_alerted_30min_handoff: set = set()   # model_ids already warned about 30-min burn rate
_rt_last_day: int = 0

async def _check_model_daily_goal(model_id: str):
    """
    Called immediately after every report_tokens write.
    Checks today's burned tokens for this model against its dailyTokenGoal.
    Any exception is printed so failures are visible in the terminal.
    """
    try:
        await _check_model_daily_goal_inner(model_id)
    except Exception as exc:
        print(f"[rt_goal] ERROR in goal check for {model_id}: {exc}", flush=True)


def _mins_since_last_save() -> float | None:
    """
    Returns minutes since the last save_docs write to library.md or PROGRESS.md.
    Returns None if never saved.
    Checks both file mtime and the _Last updated_ timestamp inside PROGRESS.md.
    """
    now = time.time()
    last_ts = None

    for f in [PRUNE_LIBRARY / "library.md", PRUNE_LIBRARY / "PROGRESS.md"]:
        if f.exists():
            mtime = f.stat().st_mtime
            if last_ts is None or mtime > last_ts:
                last_ts = mtime

    # Also check embedded timestamp in PROGRESS.md (more reliable)
    prog = PRUNE_LIBRARY / "PROGRESS.md"
    if prog.exists():
        try:
            content = prog.read_text(encoding="utf-8")
            m = re.search(r'_Last updated:\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', content)
            if m:
                from datetime import datetime
                ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").timestamp()
                if last_ts is None or ts > last_ts:
                    last_ts = ts
        except Exception:
            pass

    if last_ts is None:
        return None
    return (now - last_ts) / 60


async def _check_model_daily_goal_inner(model_id: str):
    """Fires alerts at 45/70/80/90% of daily token goal in real-time."""
    global _rt_alerted_45, _rt_alerted_70, _rt_alerted_80, _rt_alerted_90, _rt_last_day, _rt_model_burned, _rt_alerted_30min_handoff

    # Reset at midnight
    today = time.gmtime().tm_yday
    if today != _rt_last_day:
        _rt_alerted_45.clear()
        _rt_alerted_70.clear()
        _rt_alerted_80.clear()
        _rt_alerted_90.clear()
        _rt_alerted_30min_handoff.clear()
        _rt_model_burned.clear()
        _rt_last_day = today

    user_models = _load_user_models()
    goal_entry  = next((m for m in user_models if m.get("model") == model_id), None)
    if not goal_entry:
        return
    goal = int(goal_entry.get("dailyTokenGoal") or 0)
    if not goal:
        return

    label  = goal_entry.get("label", model_id)
    burned = _rt_model_burned.get(model_id, 0)
    if not burned:
        return
    pct = burned / goal * 100
    ts  = time.strftime("%H:%M")
    print(f"[rt_goal] {model_id} — {burned:,}/{goal:,} tokens ({pct:.1f}%)", flush=True)

    # 45% heads-up
    if pct >= 45 and model_id not in _rt_alerted_45:
        _rt_alerted_45.add(model_id)
        await _push_notification("info",
            f"📈 **{label}** has used {pct:.0f}% of its daily token goal "
            f"({burned:,} of {goal:,} tokens).\n\n"
            f"You're almost halfway — consider switching to a medium model for efficient token usage."
        )
        print(f"[rt_goal {ts}] 45% reached for {model_id}", flush=True)

    # 70% early warning
    if pct >= 70 and model_id not in _rt_alerted_70:
        _rt_alerted_70.add(model_id)
        await _push_notification("info",
            f"📊 **{label}** has used {pct:.0f}% of its daily token goal "
            f"({burned:,} of {goal:,} tokens).\n\n"
            f"You still have 30% left — wrap up long tasks, prefer focused questions.\n"
            f"Run **save docs** to preserve your session progress."
        )
        print(f"[rt_goal {ts}] 70% reached for {model_id}", flush=True)

    # 80% soft alert
    if pct >= 80 and model_id not in _rt_alerted_80:
        _rt_alerted_80.add(model_id)
        simple = _suggest_model("simple", user_models)
        suggestion = (f" Switch to **{simple['label']}** (`{simple['model']}`) for remaining tasks."
                      if simple and simple["model"] != model_id else "")
        await _push_notification("info",
            f"⚡ **{label}** has used {pct:.0f}% of its daily goal ({burned:,} of {goal:,} tokens).\n\n"
            f"Run **save docs** now. Conserve budget: stick to simple tasks.{suggestion}"
        )
        print(f"[rt_goal {ts}] 80% reached for {model_id}", flush=True)

    # 90% hard alert
    if pct >= 90 and model_id not in _rt_alerted_90:
        _rt_alerted_90.add(model_id)
        medium = _suggest_model("medium", user_models)
        suggestion = (f"\n\nNext session use **{medium['label']}** (`{medium['model']}`)."
                      if medium and medium["model"] != model_id else "")
        await _push_notification("warning",
            f"⚠️ **{label}** is at {pct:.0f}% of daily goal ({burned:,} of {goal:,} tokens).\n\n"
            f"Run **save docs** then click 'Project Scan' in the dashboard.{suggestion}"
        )
        print(f"[rt_goal {ts}] 90% reached for {model_id}", flush=True)


# ── Tool: session_start ──────────────────────────────────────────────

async def session_start(arguments: dict) -> dict:
    """LLM calls this immediately on connect to log login and seed daily token count."""
    model     = (arguments.get("model")     or "unknown").strip()
    timestamp = (arguments.get("timestamp") or "").strip()
    session_login(model, timestamp)
    burned = _rt_model_burned.get(model, 0)
    result = {
        "status":        "logged_in",
        "model":         model,
        "burned_today":  burned,
        "message":       f"Session started. {burned:,} tokens already burned today for {model}.",
        "llm_time":      timestamp,
        "server_time":   time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    handoff = _handoff_suggestion(model)
    if handoff:
        result["handoff_suggestion"] = handoff
        result["user_message"] = (
            f"⚠️  You are starting a session with a high-reasoning model (`{model}`). "
            f"For most coding tasks, a medium complexity model from your PruneTool config "
            f"delivers the same results at significantly lower cost. Consider switching before you begin."
        )
        result["instruction"] = "Print the user_message above verbatim to the user. Do not paraphrase."
    return result


# ── Tool: session_end ────────────────────────────────────────────────

async def session_end(arguments: dict) -> dict:
    """LLM calls this when user exits to log logout."""
    model     = (arguments.get("model")     or "unknown").strip()
    timestamp = (arguments.get("timestamp") or "").strip()
    session_logout(model, timestamp)
    burned = _rt_model_burned.get(model, 0)
    return {
        "status":       "logged_out",
        "model":        model,
        "burned_today": burned,
        "message":      f"Session ended. {burned:,} tokens burned today for {model}.",
        "llm_time":     timestamp,
        "server_time":  time.strftime("%Y-%m-%d %H:%M:%S"),
    }


# ── Tool: check_sync_status ─────────────────────────────────────────

async def check_sync_status(arguments: dict) -> dict:
    """
    Called by the LLM when the user says exit/quit/bye/done/finished.
    Logs the session logout and checks how long ago prune library/ files were last saved.
    If > 15 minutes, returns needs_save=True so the LLM blocks the exit.
    """
    model = (arguments.get("model") or "unknown").strip()

    now = time.time()

    # Check last modified time of library.md and PROGRESS.md
    files_to_check = {
        "library.md":  PRUNE_LIBRARY / "library.md",
        "PROGRESS.md": PRUNE_LIBRARY / "PROGRESS.md",
    }

    last_save_ts   = None
    last_save_file = None

    for name, path in files_to_check.items():
        if path.exists():
            mtime = path.stat().st_mtime
            if last_save_ts is None or mtime > last_save_ts:
                last_save_ts   = mtime
                last_save_file = name

    # Also check for _Last updated_ timestamp inside PROGRESS.md (more reliable)
    prog_file = PRUNE_LIBRARY / "PROGRESS.md"
    if prog_file.exists():
        try:
            content = prog_file.read_text(encoding="utf-8")
            m = re.search(r'_Last updated:\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', content)
            if m:
                from datetime import datetime
                parsed = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
                ts = parsed.timestamp()
                if last_save_ts is None or ts > last_save_ts:
                    last_save_ts   = ts
                    last_save_file = "PROGRESS.md (timestamp)"
        except Exception:
            pass

    if last_save_ts is None:
        # Never saved
        return {
            "needs_save":   True,
            "can_exit":     False,
            "reason":       "prune library has never been saved this session.",
            "last_save":    None,
            "mins_ago":     None,
            "user_message": "As you are exiting terminal, please make sure to save your work by giving command to LLMs 'save docs' for safer side.",
        }

    mins_ago = (now - last_save_ts) / 60

    if mins_ago > threshold_mins:
        mins_str = f"{mins_ago:.0f} minutes"
        return {
            "needs_save":   True,
            "can_exit":     False,
            "reason":       f"Last save was {mins_str} ago ({last_save_file}).",
            "last_save":    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last_save_ts)),
            "mins_ago":     round(mins_ago, 1),
            "threshold_mins": threshold_mins,
            "user_message": "As you are exiting terminal, please make sure to save your work by giving command to LLMs 'save docs' for safer side.",
        }

    return {
        "needs_save":   False,
        "can_exit":     True,
        "last_save":    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last_save_ts)),
        "mins_ago":     round(mins_ago, 1),
        "user_message": "As you are exiting terminal, please make sure to save your work by giving command to LLMs 'save docs' for safer side.",
    }


# ── Tool: save_docs ─────────────────────────────────────────────────

async def save_docs(arguments: dict) -> dict:
    """
    Called by the LLM to persist session knowledge to prune library/.
    The LLM supplies the rendered markdown; the server does the file I/O.

    Arguments:
      library_entry   — full markdown block to APPEND to library.md
      progress_update — surgical markdown lines to merge into PROGRESS.md
                        (optional; omit if nothing changed in PROGRESS.md)
    """
    library_entry   = (arguments.get("library_entry") or "").strip()
    progress_update = (arguments.get("progress_update") or "").strip()
    model           = (arguments.get("model") or "unknown").strip()
    target          = (arguments.get("target") or "library").strip().lower()

    if not library_entry:
        return {"error": "library_entry is required — provide the session summary markdown."}

    saved = []
    errors = []

    save_ts = time.strftime("%Y-%m-%d %H:%M:%S")
    model_tag = f" · {model}" if model and model != "unknown" else ""

    # ── library.md — overwrite with latest overall summary ───────────
    lib_file = PRUNE_LIBRARY / "library.md"
    try:
        PRUNE_LIBRARY.mkdir(parents=True, exist_ok=True)
        cleaned_entry = re.sub(r'^\s*#+\s*(Session\b.*|[A-Z][a-z]+ \d+,?\s*\d{4}.*|\d{4}-\d{2}-\d{2}.*)\n?', '', library_entry, flags=re.MULTILINE)
        cleaned_entry = re.sub(r'^\s*\*{1,3}Session\b.*?\*{0,3}:?.*\n?', '', cleaned_entry, flags=re.MULTILINE | re.IGNORECASE)
        cleaned_entry = re.sub(r'^\s*\*{1,3}\d{4}-\d{2}-\d{2}.*\n?', '', cleaned_entry, flags=re.MULTILINE)
        cleaned_entry = re.sub(r'^\s*\*{1,3}[A-Z][a-z]+ \d+,?\s*\d{4}.*\n?', '', cleaned_entry, flags=re.MULTILINE)
        cleaned_entry = cleaned_entry.lstrip('\n')
        stamped_entry = f"{cleaned_entry}\n\n_Saved: {save_ts}{model_tag}_"
        lib_file.write_text(stamped_entry, encoding="utf-8")
        saved.append(str(lib_file))
        log.info("[save_docs] Overwrote library.md with latest summary")
    except Exception as exc:
        errors.append(f"library.md: {exc}")

    # ── PROGRESS.md — overwrite, strip any date/session headers LLM adds ──
    if progress_update:
        prog_file = PRUNE_LIBRARY / "PROGRESS.md"
        try:
            # Strip date/session headers the LLM tends to add at the top
            cleaned = re.sub(r'^\s*#+\s*(Session\b.*|[A-Z][a-z]+ \d+,?\s*\d{4}.*|\d{4}-\d{2}-\d{2}.*)\n?', '', progress_update, flags=re.MULTILINE)
            cleaned = re.sub(r'^\s*\*{1,3}Session\b.*?\*{0,3}:?.*\n?', '', cleaned, flags=re.MULTILINE | re.IGNORECASE)
            cleaned = re.sub(r'^\s*\*{1,3}\d{4}-\d{2}-\d{2}.*\n?', '', cleaned, flags=re.MULTILINE)
            cleaned = re.sub(r'^\s*\*{1,3}[A-Z][a-z]+ \d+,?\s*\d{4}.*\n?', '', cleaned, flags=re.MULTILINE)
            cleaned = cleaned.lstrip('\n')
            stamp = f"\n\n_Last updated: {save_ts}{model_tag}_"
            prog_file.write_text(cleaned + stamp, encoding="utf-8")
            saved.append(str(prog_file))
            log.info("[save_docs] Updated %s", prog_file)
        except Exception as exc:
            errors.append(f"PROGRESS.md: {exc}")

    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    result = {
        "saved_at": ts,
        "files_written": saved,
        "status": "ok" if not errors else "partial",
    }
    if errors:
        result["errors"] = errors
    else:
        result["confirm"] = "Saved to prune library/ successfully."
        result["user_message"] = (
            "✅ Docs saved to prune library/.\n\n"
            "👉 Please click **Project Scan** in the PruneTool dashboard to refresh the knowledge base."
        )
        result["instruction"] = "Print the user_message above verbatim to the user. Do not paraphrase."

    handoff = _handoff_suggestion(model)
    if handoff:
        result["handoff_suggestion"] = handoff

    # Print to terminal so user sees it happened
    print(_box_top(f"SAVE DOCS  [{ts}]  {model}"), flush=True)
    for f in saved:
        print(_box_row(f"  Written: {f}"), flush=True)
    if handoff:
        print(_box_row(f"  Handoff  : {handoff.get('user_message', '')}"), flush=True)
    if errors:
        for e in errors:
            print(_box_row(f"  ERROR: {e}"), flush=True)
    print(_box_bot(), flush=True)

    return result


# ── Tool 3: analyze_complexity ───────────────────────────────────────

def _load_user_models() -> List[dict]:
    """
    Read the user's llms_prunetoolfinder.js and return the models list.
    Each entry has: id, label, model, complexity (simple/medium/complex).
    Returns [] if file missing or parse fails.
    """
    if not USER_FINDER_JS.exists():
        return []
    try:
        data = _parse_llm_finder(USER_FINDER_JS)
        return data.get("models", [])
    except Exception:
        return []


def _suggest_model(complexity_tier: str, user_models: List[dict]) -> dict:
    """
    Find the best user model for the given complexity tier.
    Falls through to simpler tiers if the exact tier has no match.
    Returns a dict with 'model', 'label', 'id' keys, or empty dict.
    """
    # Try exact tier first, then fall back
    tier_order = {
        "simple":  ["simple", "medium", "complex"],
        "medium":  ["medium", "simple", "complex"],
        "complex": ["complex", "medium", "simple"],
    }
    for tier in tier_order.get(complexity_tier, [complexity_tier]):
        for m in user_models:
            if m.get("complexity") == tier:
                return {
                    "id":    m.get("id", ""),
                    "label": m.get("label", m.get("model", "")),
                    "model": m.get("model", ""),
                }
    return {}


def _handoff_suggestion(calling_model: str) -> dict:
    """
    If the calling model is complex tier and a medium model exists in
    llms_prunetoolfinder.js, return a handoff suggestion dict.
    Returns {} if no suggestion needed.
    """
    user_models = _load_user_models()
    calling_entry = next((m for m in user_models if m.get("model") == calling_model), None)
    if not calling_entry or calling_entry.get("complexity") != "complex":
        return {}
    medium = _suggest_model("medium", user_models)
    if not medium or medium["model"] == calling_model:
        return {}
    return {
        "user_message": (
            f"⚠️ You're using a high-reasoning model (`{calling_model}`) for this task. "
            f"Consider switching to a medium complexity model from your PruneTool config "
            f"for most coding tasks — same results, lower cost."
        ),
    }


async def analyze_complexity(arguments: dict) -> dict:
    calling_model = (arguments.get("model") or "unknown").strip()
    sk_data = _load_json(PRUNETOOL_DATA / "skeleton.json")
    fm      = _load_json(PRUNETOOL_DATA / "folder_map.json")
    meta    = _load_json(PRUNETOOL_DATA / "project_metadata.json") or {}

    if not sk_data:
        return {"error": "skeleton.json not found"}

    entries       = sk_data.get("entries", [])
    total_symbols = len(entries)
    total_files   = len(set(e.get("file_path","") for e in entries))
    total_edges   = len(fm.get("edges", [])) if fm else 0
    total_folders = len(fm.get("folders", {})) if fm else 0

    # Determine complexity tier from graph stats
    if total_symbols <= 500 and total_edges <= 5:
        tier      = "simple"
        rationale = (f"Small scope: {total_symbols} symbols, {total_edges} edges. "
                     "Use a fast/cheap model.")
    elif total_symbols <= 2000 and total_edges <= 20:
        tier      = "medium"
        rationale = (f"Medium scope: {total_symbols} symbols, {total_edges} edges. "
                     "A mid-tier model balances cost and reasoning.")
    else:
        tier      = "complex"
        rationale = (f"High complexity: {total_symbols} symbols, {total_edges} edges, "
                     f"{total_files} files. Use your most capable model.")

    # Look up the user's configured model for this tier
    user_models   = _load_user_models()
    suggested     = _suggest_model(tier, user_models)
    finder_exists = USER_FINDER_JS.exists()

    result: dict = {
        "stats": {
            "total_symbols":      total_symbols,
            "total_files":        total_files,
            "total_folders":      total_folders,
            "cross_folder_edges": total_edges,
            "last_scanned":       meta.get("last_scanned_at"),
        },
        "complexity_tier": tier,
        "rationale":       rationale,
    }

    if suggested:
        result["suggested_model"] = suggested
        result["suggestion_note"] = (
            f"Based on your llms_prunetoolfinder.js, use '{suggested['label']}' "
            f"({suggested['model']}) for {tier} queries like this one."
        )
    elif finder_exists and not user_models:
        result["suggestion_note"] = (
            "llms_prunetoolfinder.js exists but has no models configured. "
            f"Open {USER_FINDER_JS} and uncomment the models you have."
        )
    else:
        result["suggestion_note"] = (
            f"No llms_prunetoolfinder.js found in {CODEBASE_ROOT}. "
            "It will be created on next startup — open it and uncomment your models."
        )

    result["handoff_suggestion"] = _handoff_suggestion(calling_model)
    return result


# ════════════════════════════════════════════════════════════════════
# TOOL REGISTRY + SCHEMAS
# ════════════════════════════════════════════════════════════════════

TOOL_REGISTRY = {
    "describe_project":   describe_project,
    "analyze_complexity": analyze_complexity,
    "report_tokens":      report_tokens,
    "save_docs":          save_docs,
    "session_start":      session_start,
    "session_end":        session_end,
    "check_sync_status":  check_sync_status,
}

TOOL_SCHEMAS = {
    "describe_project": {
        "name": "describe_project",
        "description": (
            "Returns full project context: folder map, symbol index, prune library docs. "
            "IMPORTANT: Pass 'since_version' if you already have context from a previous call. "
            "- No since_version → full context (~5500 tokens, first call only). "
            "- since_version matches current → {status:'up_to_date'} (~30 tokens, free check). "
            "- since_version differs → only changed sections (~300 tokens). "
            "Always store the returned 'version' and pass it as 'since_version' on every future call."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "model": {
                    "type": "string",
                    "description": "Your model name (e.g. claude-sonnet-4-6, gpt-4o, gemini-2.0-flash).",
                },
                "since_version": {
                    "type": "string",
                    "description": "The version string returned by your last describe_project call. Pass this to get delta-only updates instead of the full 5500-token context.",
                },
                "timestamp": {
                    "type": "string",
                    "description": "Current date and time (e.g. '2026-04-09 15:30:00 Wednesday').",
                },
            },
            "required": [],
        },
    },
    "analyze_complexity": {
        "name": "analyze_complexity",
        "description": (
            "Analyse the Knowledge Graph (symbols, files, cross-folder edges) and suggest "
            "which model to use based on query complexity. Reads the user's "
            "llms_prunetoolfinder.js to return a specific model suggestion."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "model": {
                    "type": "string",
                    "description": "Your exact model ID — used to check if you're over-qualified for this project and suggest a cheaper alternative.",
                },
            },
            "required": [],
        },
    },
    "report_tokens": {
        "name": "report_tokens",
        "description": (
            "REQUIRED: Call this after EVERY response you send to the user — no exceptions. "
            "Pass input_tokens (prompt tokens), output_tokens (completion tokens), and model (your exact model ID). "
            "This tracks usage and alerts you when to save docs. "
            "Optional: 'cached_input_tokens' (Anthropic cache_read_input_tokens) — billed at 0.1x, "
            "server applies discount so cached tokens don't count against your daily goal."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "input_tokens": {
                    "type":        "integer",
                    "description": "Total input (prompt) tokens in your last response.",
                },
                "output_tokens": {
                    "type":        "integer",
                    "description": "Output (completion) tokens in your last response.",
                },
                "cached_input_tokens": {
                    "type":        "integer",
                    "description": "Input tokens served from prompt cache (billed at 0.1x). "
                                   "For Anthropic: use cache_read_input_tokens from the usage object.",
                },
                "model": {
                    "type":        "string",
                    "description": "Your exact model ID (e.g. claude-sonnet-4-6, gpt-4o, gemini-2.0-flash).",
                },
                "fade_response": {
                    "type":        "string",
                    "enum":        ["yes", "no", "auto"],
                    "description": (
                        "Only send this when you received a fade_check=true response asking about context refresh. "
                        "'yes' = refresh now, 'no' = skip, 'auto' = refresh automatically from now on."
                    ),
                },
            },
            "required": ["input_tokens", "output_tokens", "model"],
        },
    },
    "save_docs": {
        "name": "save_docs",
        "description": (
            "Persist the session knowledge log to prune library/. "
            "Call this when the user says 'save docs', '/save docs', or when report_tokens "
            "returns SAVE_NOW=true. "
            "Scroll back through the ENTIRE conversation — every file touched, decision made, "
            "bug fixed, approach tried — then pass the rendered markdown here. "
            "The server writes the files. User should click Project Scan in the dashboard to update the index."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "library_entry": {
                    "type": "string",
                    "description": (
                        "REPLACES prune library/library.md entirely — write a single cumulative summary "
                        "covering ALL work done across ALL sessions, not just this one. "
                        "Before writing, read the existing library.md content returned in describe_project "
                        "and MERGE it with what was done this session. The result should be one cohesive "
                        "document a future LLM can read to understand the full project history.\n"
                        "Use this structure:\n"
                        "# Project Knowledge\n\n"
                        "## What Was Built\n- <all major features/changes, newest first>\n\n"
                        "## Key Decisions\n- <important architectural or design decisions>\n\n"
                        "## Files of Note\n- `path/to/file` — <what it does>\n\n"
                        "## Bugs Fixed\n- <symptom, root cause, fix>\n\n"
                        "## Next Steps\n- <specific actionable items>"
                    ),
                },
                "progress_update": {
                    "type": "string",
                    "description": (
                        "REPLACES prune library/PROGRESS.md entirely. Write the full current "
                        "status — merge old state with this session's changes into one document. "
                        "Do NOT start with a date or session header. Do NOT add '## 2026-...' or "
                        "'## Session X' blocks. Just the content — the server appends the timestamp."
                    ),
                },
                "model": {
                    "type":        "string",
                    "description": "Your model name (e.g. claude-sonnet-4-6, gpt-4o, gemini-2.0-flash).",
                },
                "target": {
                    "type":        "string",
                    "enum":        ["library"],
                    "description": (
                        "Where to save: 'library' (default) appends to library.md. "
                        "use this when instructed by the 80%/90% daily goal alert."
                    ),
                },
            },
            "required": ["library_entry"],
        },
    },
    "session_start": {
        "name": "session_start",
        "description": (
            "MANDATORY — Call this FIRST before answering any user message. "
            "Logs your session start and seeds today's token count. "
            "Pass your exact model ID and current timestamp. "
            "Do NOT call describe_project automatically — only when the user explicitly asks."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "model": {
                    "type": "string",
                    "description": "Your exact model ID (e.g. claude-sonnet-4-6, gpt-4o).",
                },
                "timestamp": {
                    "type": "string",
                    "description": "Current date, time and day when connecting (e.g. '2026-04-09 15:30:00 Wednesday').",
                },
            },
            "required": ["model", "timestamp"],
        },
    },
    "session_end": {
        "name": "session_end",
        "description": (
            "Call this when the user confirms they are exiting. "
            "Logs your logout with timestamp and records total tokens burned today."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "model": {
                    "type": "string",
                    "description": "Your exact model ID.",
                },
                "timestamp": {
                    "type": "string",
                    "description": "Current date, time and day when exiting (e.g. '2026-04-09 16:45:00 Wednesday').",
                },
            },
            "required": ["model", "timestamp"],
        },
    },
    "check_sync_status": {
        "name": "check_sync_status",
        "description": (
            "MANDATORY EXIT GATE — call this BEFORE saying goodbye, BEFORE acknowledging any exit. "
            "Trigger words: exit, quit, bye, goodbye, done, finished, cya, see you, talk later, closing, shutting down. "
            "You MUST call this tool first, then show the user_message from the response, "
            "then ask the user to confirm. Never skip this step. Never say goodbye in the same "
            "turn the user first says an exit word — always call this tool first."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "model": {
                    "type":        "string",
                    "description": "Your model ID — used to log the session logout.",
                },
            },
            "required": [],
        },
    },
}


# ════════════════════════════════════════════════════════════════════
# MCP JSON-RPC 2.0 DISPATCH
# ════════════════════════════════════════════════════════════════════

async def _dispatch(method: str, params: dict) -> Any:
    if method == "initialize":
        return {
            "protocolVersion": "2025-03-26",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "prunetool-mcp", "version": "1.1.0"},
            "instructions": (
                "You are connected to PruneTool MCP. These are MANDATORY rules — follow them every session, no exceptions:\n\n"
                "RULE 1 — SESSION START (before your first response):\n"
                "  Call 'session_start' with your model ID and current timestamp (e.g. '2026-04-10 10:00:00 Thursday').\n"
                "  Do NOT call 'describe_project' automatically — only when the user explicitly asks.\n\n"
                "RULE 2 — AFTER EVERY RESPONSE (no exceptions):\n"
                "  Call 'report_tokens' with: input_tokens, output_tokens, model.\n"
                "  Do NOT pass user_message. This is required for token tracking and alerting.\n\n"
                "RULE 3 — IF report_tokens RETURNS SAVE_NOW=true:\n"
                "  Print the user_message field verbatim. Do not paraphrase.\n"
                "  Ask the user to type '/save docs' before continuing.\n\n"
                "RULE 4 — WHEN USER TYPES '/save docs':\n"
                "  Scroll back through the entire conversation.\n"
                f"  Merge all session knowledge into {PRUNE_LIBRARY / 'library.md'} and {PRUNE_LIBRARY / 'PROGRESS.md'}.\n"
                "  Structure: What Was Built · Key Decisions · Files of Note · Bugs Fixed · Next Steps.\n"
                "  Call 'save_docs' with the merged content. Confirm when done.\n"
            ),
        }
    if method == "tools/list":
        return {"tools": list(TOOL_SCHEMAS.values())}
    if method == "tools/call":
        name = params.get("name", "")
        args = params.get("arguments", {})
        handler = TOOL_REGISTRY.get(name)
        if not handler:
            raise ValueError(f"Unknown tool: {name!r}")
        t0 = time.monotonic()
        log_call(name, args)                              # ← terminal print
        result = await handler(args)
        elapsed = time.monotonic() - t0
        log_response(name, result, elapsed)               # ← terminal print
        return {
            "content": [{"type": "text",
                         "text": json.dumps(result, indent=2, ensure_ascii=False)}]
        }
    if method == "resources/list":
        return {"resources": []}
    raise ValueError(f"Unsupported method: {method!r}")


async def _handle_single(req: dict) -> dict:
    req_id = req.get("id")
    method = req.get("method", "")
    params = req.get("params", {})
    try:
        result = await _dispatch(method, params)
        return {"jsonrpc": "2.0", "id": req_id, "result": result}
    except ValueError as exc:
        return {"jsonrpc": "2.0", "id": req_id,
                "error": {"code": -32601, "message": str(exc)}}
    except Exception as exc:
        log.exception("Internal error in %r", method)
        return {"jsonrpc": "2.0", "id": req_id,
                "error": {"code": -32603, "message": f"Internal error: {exc}"}}


# ════════════════════════════════════════════════════════════════════
# WATCHDOG
# ════════════════════════════════════════════════════════════════════

async def _push_library_to_annotations():
    """
    Read prune library markdown docs and push key sections as folder
    annotations to the gateway. This makes the LLM's project knowledge
    (architecture, decisions, folder notes) available to the Scout LLM
    during auto-annotation, so the Knowledge Graph reflects it.
    """
    if not PRUNE_LIBRARY.exists():
        return

    annotations: Dict[str, str] = {}

    for md_file in sorted(PRUNE_LIBRARY.glob("*.md")):
        try:
            text = md_file.read_text(encoding="utf-8")
        except OSError:
            continue

        current_section = None
        current_lines: List[str] = []

        for line in text.splitlines():
            if line.startswith("## "):
                # Save previous section
                if current_section and current_lines:
                    content = "\n".join(current_lines).strip()
                    if content:
                        key = f"prune-library/{md_file.stem}/{current_section}"
                        annotations[key] = content[:400]
                current_section = line[3:].strip()
                current_lines = []
            elif current_section:
                current_lines.append(line)

        # Save last section
        if current_section and current_lines:
            content = "\n".join(current_lines).strip()
            if content:
                key = f"prune-library/{md_file.stem}/{current_section}"
                annotations[key] = content[:400]

    if not annotations:
        log_watchdog("Prune library has no content yet — skipping annotation push")
        return

    # Push each section as a gateway annotation
    pushed = 0
    async with httpx.AsyncClient() as client:
        for path_key, note in annotations.items():
            try:
                r = await client.post(
                    f"{GATEWAY_URL}/annotations",
                    json={"path": path_key, "note": note},
                    timeout=10.0,
                )
                if r.status_code < 300:
                    pushed += 1
            except Exception:
                pass

    log_watchdog(
        f"Pushed {pushed}/{len(annotations)} prune library sections "
        f"as Scout annotations — Scout will use these during auto-annotation"
    )


class _LibraryHandler(FileSystemEventHandler if WATCHDOG_AVAILABLE else object):
    def __init__(self, loop: asyncio.AbstractEventLoop):
        if WATCHDOG_AVAILABLE:
            super().__init__()
        self._loop = loop

    def on_modified(self, event):
        try:
            if sys.is_finalizing():
                return
            if event.is_directory:
                return
            p = Path(event.src_path)
            if p.suffix not in {".md", ".json", ".txt"}:
                return
            log_watchdog(f"Change detected: {p.name}")
            pre_snap = _snapshot_docs()
            asyncio.run_coroutine_threadsafe(
                self._debounce(p, pre_snap), self._loop
            )
        except Exception:
            pass

    async def _debounce(self, path: Path, pre_snap: Dict[str, dict]):
        global _doc_snapshot
        # Wait for the LLM to finish writing
        log_watchdog(f"Change detected in {path.name} — waiting 3s for write to settle")
        await asyncio.sleep(3)

        post_snap = _snapshot_docs()

        changes = _diff_snapshots(pre_snap, post_snap)
        if not changes:
            log_watchdog(f"No real change detected — all doc hashes unchanged")
            return

        log_watchdog(f"Docs updated ({len(changes)} file(s) changed):")
        for line in changes:
            log_watchdog(line)

        _doc_snapshot = post_snap

        # Prune library changed — notify the dashboard so the user can
        # manually trigger a rescan. Do NOT auto-rescan.
        log_watchdog("Prune library updated — notifying dashboard to request rescan...")
        try:
            async with httpx.AsyncClient() as client:
                r = await client.post(
                    f"{GATEWAY_URL}/rescan-needed",
                    json={"reason": "Prune library was updated — rescan to apply changes to Knowledge Graph"},
                    timeout=10.0,
                )
            if r.status_code < 300:
                log_watchdog("Dashboard notified — user will see rescan prompt near Scan Project button.")
            else:
                log_watchdog(f"/rescan-needed returned HTTP {r.status_code}")
        except Exception as exc:
            log_watchdog(f"Could not notify dashboard: {exc}")

        log_watchdog("Watchdog ready for next /save docs.")


def start_watchdog(loop: asyncio.AbstractEventLoop):
    if not WATCHDOG_AVAILABLE:
        log.warning("[watchdog] not available — pip install watchdog")
        return None
    if not PRUNE_LIBRARY.exists():
        log.warning("[watchdog] prune library/ not found at %s", PRUNE_LIBRARY)
        return None
    global _doc_snapshot
    _doc_snapshot = _snapshot_docs()   # baseline — so first run has something to diff against
    observer = Observer()
    observer.schedule(_LibraryHandler(loop), str(PRUNE_LIBRARY), recursive=False)
    observer.start()
    log_watchdog(f"Monitoring: {PRUNE_LIBRARY}")
    return observer


async def scan_monitor_task():
    """
    Persistent background task — polls /scan-status every 6s.
    Prints progress to MCP terminal whenever a scan is running,
    regardless of whether it was triggered manually (UI button)
    or automatically (watchdog after /save docs).
    """
    last_stage = "idle"
    last_msg   = ""

    while True:
        await asyncio.sleep(6)
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(f"{GATEWAY_URL}/scan-status", timeout=5.0)
            status = r.json()
        except Exception:
            continue

        stage   = status.get("stage", "idle")
        message = status.get("message", "")

        # Nothing to report while idle
        if stage == "idle" and last_stage == "idle":
            continue

        changed = (stage != last_stage) or (
            stage == "annotating" and message != last_msg
        )

        if changed:
            annotated = status.get("annotated", 0)
            total_ann = status.get("total_to_annotate", 0)
            files     = status.get("files_found", 0)
            symbols   = status.get("symbols_found", 0)

            if stage == "loading_library":
                log_watchdog("Step 1/4  Reading prune library docs...")
            elif stage == "scanning":
                log_watchdog("Step 2/4  Indexing files in project...")
            elif stage == "building_map":
                log_watchdog(f"Step 3/4  Building folder map — {files} files, {symbols} symbols indexed")
            elif stage == "annotating":
                if total_ann and annotated:
                    pct = int(annotated / total_ann * 100)
                    log_watchdog(f"Step 4/4  Auto-annotating... {annotated}/{total_ann} files ({pct}%)")
                else:
                    log_watchdog(f"Step 4/4  Auto-annotating {total_ann} files for Knowledge Graph...")
            elif stage == "complete":
                elapsed = ""
                if status.get("started_at") and status.get("finished_at"):
                    secs = int(status["finished_at"] - status["started_at"])
                    elapsed = f" in {secs}s"
                log_watchdog(
                    f"Knowledge Graph ready{elapsed} — "
                    f"{files} files · {symbols} symbols · {annotated} files annotated"
                )
                await _push_notification(
                    "info",
                    f"Knowledge Graph fully ready. "
                    f"{files} files · {symbols} symbols · {annotated} auto-annotated. "
                    f"Knowledge Graph is now at full accuracy."
                )
            elif stage == "idle" and last_stage not in ("idle", "complete"):
                # Scan ended without a complete signal
                log_watchdog("Scan finished.")

            last_stage = stage
            last_msg   = message


# ════════════════════════════════════════════════════════════════════
# TOKEN MONITOR
# ════════════════════════════════════════════════════════════════════

_session_start: float  = time.time()   # reset after each alert
_session_tokens: int   = 0             # cumulative tokens reported by LLM this session


async def token_monitor_task():
    global _session_start
    log.info("[token_monitor] started")
    while True:
        await asyncio.sleep(TOKEN_POLL_SECONDS)
        if not TOKEN_LOG.exists():
            continue

        # Sum all tokens since session start (not a rolling window)
        total = 0
        try:
            for line in TOKEN_LOG.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                    ts = float(e.get("ts", e.get("timestamp", 0)))
                    if ts >= _session_start:
                        total += int(e.get("tokens", e.get("token_count", 0)))
                except (json.JSONDecodeError, ValueError):
                    continue
        except OSError:
            continue

        log.debug("[token_monitor] session tokens so far: %d", total)

        if total >= TOKEN_ALERT_THRESHOLD:
            msg = (
                f"35,000 token threshold reached this session ({total:,} tokens used). "
                f"Tell the user to type /save docs — the LLM should write all progress "
                f"and decisions into the prune library folder before context is lost."
            )
            log_alert(msg)
            await _push_notification("warning", msg)
            # Reset session counter so next 35k block triggers a new alert
            _session_start = time.time()
            log.info("[token_monitor] Session counter reset after alert.")


# ════════════════════════════════════════════════════════════════════
# AUTO-SAVE REMINDER TASK
# ════════════════════════════════════════════════════════════════════

AUTO_SAVE_INTERVAL = 15 * 60   # every 15 minutes


async def auto_save_task():
    """
    Every 30 minutes, push a save-docs instruction to the connected LLM
    AND inject a visible reminder into all active PTY terminal sessions
    via the gateway broadcast endpoint.
    """
    log.info("[auto_save] started — will remind every %d min", AUTO_SAVE_INTERVAL // 60)
    while True:
        await asyncio.sleep(AUTO_SAVE_INTERVAL)
        ts = time.strftime("%H:%M:%S")

        # MCP-connected LLMs: push via SSE with explicit action
        msg = (
            "30-minute auto-save checkpoint — save session memory to prune library NOW. "
            "Append a dated entry to prune library/library.md (what was discussed, built, "
            "decided, bugs fixed). Update prune library/PROGRESS.md Current Status. "
            "Do this silently without waiting for the user to ask."
        )
        log_alert(f"[{ts}] Auto-save checkpoint — instructing LLM to update prune library")
        await _push_notification("info", msg)

        pass


# ════════════════════════════════════════════════════════════════════
# TOKEN USAGE SUMMARY — prints every 30 min
# ════════════════════════════════════════════════════════════════════

TOKEN_SUMMARY_INTERVAL = 30 * 60   # every 30 minutes
DAILY_TOKEN_BUDGET     = 200_000   # rough Claude Code daily limit (adjust as needed)


async def token_summary_task():
    """
    Every 30 min, print a formatted token usage report to the MCP terminal.
    Reads token_log.jsonl and shows:
      - Tokens used in the last 30 min window
      - Tokens used today (since midnight)
      - % of daily budget used
      - Warning if approaching limit
    """
    log.info("[token_summary] started — report every %d min", TOKEN_SUMMARY_INTERVAL // 60)

    while True:
        await asyncio.sleep(TOKEN_SUMMARY_INTERVAL)

        if not TOKEN_LOG.exists():
            continue

        now      = time.time()
        midnight = now - (now % 86400)          # start of today (UTC)
        window   = now - TOKEN_SUMMARY_INTERVAL  # last 30 min

        tokens_window = 0
        tokens_today  = 0
        entries_today = []

        try:
            for line in TOKEN_LOG.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    e  = json.loads(line)
                    ts = float(e.get("ts", e.get("timestamp", 0)))
                    t  = int(e.get("tokens", e.get("token_count", 0)))
                    if ts >= midnight:
                        tokens_today += t
                        entries_today.append((ts, t, e.get("query", "")))
                    if ts >= window:
                        tokens_window += t
                except (json.JSONDecodeError, ValueError):
                    continue
        except OSError:
            continue

        pct    = (tokens_today / DAILY_TOKEN_BUDGET * 100) if DAILY_TOKEN_BUDGET else 0
        bar_w  = 30
        filled = int(bar_w * min(pct, 100) / 100)
        bar    = "█" * filled + "░" * (bar_w - filled)
        color  = "warning" if pct >= 70 else "info"

        ts_str = time.strftime("%H:%M:%S")
        sep    = "─" * 58

        print(f"\n  {sep}", flush=True)
        print(f"  [token_summary {ts_str}] 30-min Usage Report", flush=True)
        print(f"  {sep}", flush=True)
        print(f"  Last 30 min  : {tokens_window:>8,} tokens", flush=True)
        print(f"  Today total  : {tokens_today:>8,} tokens", flush=True)
        print(f"  Daily budget : {DAILY_TOKEN_BUDGET:>8,} tokens", flush=True)
        print(f"  Used [{bar}] {pct:.1f}%", flush=True)

        if pct >= 90:
            print(f"  ⚠  CRITICAL — {100-pct:.0f}% budget remaining. Save docs NOW.", flush=True)
        elif pct >= 70:
            print(f"  ⚠  WARNING  — {100-pct:.0f}% budget remaining. Consider /save docs.", flush=True)
        else:
            print(f"  ✓  Budget healthy — {100-pct:.0f}% remaining.", flush=True)

        print(f"  {sep}\n", flush=True)

        # Also push to dashboard via SSE if approaching limit
        if pct >= 70:
            await _push_notification(
                color,
                f"Token usage: {tokens_today:,}/{DAILY_TOKEN_BUDGET:,} ({pct:.0f}%) today. "
                f"Last 30 min: {tokens_window:,}. "
                + ("⚠ Save docs now before limit is hit." if pct >= 90 else "Consider /save docs soon.")
            )




# ════════════════════════════════════════════════════════════════════
# FASTAPI APP
# ════════════════════════════════════════════════════════════════════

_watchdog_observer = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _watchdog_observer

    # Print startup banner
    print(flush=True)
    print(_box_top("PruneTool MCP Server  v1.1"), flush=True)
    print(_box_row(f"Install : {ROOT}"), flush=True)
    print(_box_row(f"Project : {CODEBASE_ROOT}"), flush=True)
    print(_box_row(f"Gateway : {GATEWAY_URL}"), flush=True)
    print(_box_row(f"MCP     : POST http://localhost:8765/mcp"), flush=True)
    print(_box_sep(), flush=True)
    # Auto-create missing folders/files, then check what still needs human action
    created  = auto_setup()
    warnings, models_info = run_setup_check()
    print_setup_report(created, warnings, models_info)
    print(_box_bot(), flush=True)

    asyncio.create_task(token_monitor_task(),     name="token_monitor")
    asyncio.create_task(scan_monitor_task(),      name="scan_monitor")
    asyncio.create_task(auto_save_task(),         name="auto_save")
    asyncio.create_task(token_summary_task(),     name="token_summary")
    # daily_goal_monitor_task removed — replaced by real-time check in report_tokens + session_login seeding
    loop = asyncio.get_event_loop()
    _watchdog_observer = start_watchdog(loop)

    yield

    if _watchdog_observer and _watchdog_observer.is_alive():
        _watchdog_observer.stop()
        _watchdog_observer.join(timeout=10)
    log.info("[mcp] shutdown complete.")


app = FastAPI(
    title="PruneTool MCP Specialist Interface",
    version="1.1.0",
    description="MCP middleware between external LLMs and PruneTool project metadata.",
    lifespan=lifespan,
)
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])


# ── Main MCP endpoint ────────────────────────────────────────────────

@app.post("/mcp")
async def mcp_endpoint(request: Request) -> JSONResponse:
    """JSON-RPC 2.0 — single request or batch array."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            {"jsonrpc": "2.0", "id": None,
             "error": {"code": -32700, "message": "Parse error"}},
            status_code=400,
        )
    if isinstance(body, list):
        return JSONResponse([await _handle_single(r) for r in body])
    return JSONResponse(await _handle_single(body))


# ── SSE endpoint — LLM clients subscribe here for server notifications ──

@app.get("/sse")
async def sse_endpoint(request: Request):
    """
    Server-Sent Events stream.
    LLM clients connect here to receive server-initiated notifications
    (token alerts, watchdog events, re-scan complete, etc.)

    MCP-compatible format: each event is a JSON-RPC 2.0 notification.
    """
    queue: asyncio.Queue = asyncio.Queue(maxsize=50)
    _sse_clients.append(queue)
    log.info("[sse] Client connected (%d total)", len(_sse_clients))

    async def event_stream():
        # Send initial handshake so client knows SSE is live
        yield (
            "data: " + json.dumps({
                "jsonrpc": "2.0",
                "method":  "notifications/message",
                "params": {
                    "level":  "info",
                    "logger": "prunetool-mcp",
                    "data":   (
                        f"PruneTool MCP connected. Token monitor active — "
                        f"alert fires at {TOKEN_ALERT_THRESHOLD:,} tokens per session."
                    ),
                },
            }) + "\n\n"
        )
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"data: {payload}\n\n"
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"   # keep connection alive
        finally:
            if queue in _sse_clients:
                _sse_clients.remove(queue)
            log.info("[sse] Client disconnected (%d remaining)", len(_sse_clients))

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":               "no-cache",
            "X-Accel-Buffering":           "no",
            "Access-Control-Allow-Origin": "*",
        },
    )


# ── REST shortcuts (curl-friendly) ───────────────────────────────────

@app.get("/mcp/tools")
async def list_tools():
    return {"tools": list(TOOL_SCHEMAS.values())}

@app.get("/mcp/project")
async def project_shortcut():
    t0 = time.monotonic()
    r = await describe_project({})
    log_response("describe_project", r, time.monotonic() - t0)
    return r


@app.get("/mcp/complexity")
async def complexity_shortcut():
    t0 = time.monotonic()
    r = await analyze_complexity({})
    log_response("analyze_complexity", r, time.monotonic() - t0)
    return r


@app.get("/health")
async def health():
    wdog = _watchdog_observer is not None and _watchdog_observer.is_alive() \
           if WATCHDOG_AVAILABLE else False
    return {
        "status": "ok",
        "prunetool_install": str(ROOT),
        "target_project":   str(CODEBASE_ROOT),
        "data_path":        str(PRUNETOOL_DATA),
        "data_exists":      PRUNETOOL_DATA.exists(),
        "library_path":     str(PRUNE_LIBRARY),
        "library_exists":   PRUNE_LIBRARY.exists(),
        "watchdog_active":  wdog,
    }


# ════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ════════════════════════════════════════════════════════════════════

async def _run_stdio(json_out=None):
    """stdio transport — for Codex and any MCP client that launches us as a child process.
    Reads newline-delimited JSON-RPC from stdin, writes responses to json_out (original stdout).
    All print()/logging goes to stderr so the JSON-RPC stream stays clean.
    """
    import sys
    import io
    if json_out is None:
        json_out = sys.stdout

    # Use binary stdin for reliable line reading on Windows
    stdin_bin  = sys.stdin.buffer if hasattr(sys.stdin, 'buffer') else sys.stdin
    stdout_bin = json_out.buffer  if hasattr(json_out,  'buffer') else json_out

    loop = asyncio.get_event_loop()

    def _read_line():
        line = stdin_bin.readline()
        return line.decode('utf-8', errors='replace') if isinstance(line, bytes) else line

    while True:
        raw = await loop.run_in_executor(None, _read_line)
        if not raw:
            await asyncio.sleep(0.1)
            continue  # keep alive, don't exit on empty read
        raw = raw.strip()
        if not raw:
            continue
        try:
            body = json.loads(raw)
        except Exception:
            resp = {"jsonrpc": "2.0", "id": None,
                    "error": {"code": -32700, "message": "Parse error"}}
            stdout_bin.write((json.dumps(resp) + "\n").encode('utf-8'))
            stdout_bin.flush()
            continue
        # Notifications have no "id" — acknowledge silently, no response
        if "id" not in body:
            continue
        resp = await _handle_single(body)
        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    import sys
    if "--stdio" in sys.argv:
        # stdio mode — redirect sys.stdout to stderr so print() doesn't corrupt JSON-RPC stream
        _json_out = sys.stdout
        sys.stdout = sys.stderr
        import logging
        for h in logging.root.handlers:
            try: h.stream = sys.stderr
            except Exception: pass
        asyncio.run(_run_stdio(_json_out))
    else:
        import uvicorn
        uvicorn.run("mcp_server:app", host="0.0.0.0", port=8765,
                    reload=False, log_level="info")
