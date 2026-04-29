"""
Lightweight stdio MCP entry point for Codex and other stdio-only MCP clients.
Imports only what's needed — avoids loading FastAPI/uvicorn/watchdog at startup.
"""
import sys
import json
import asyncio
import os
from pathlib import Path

# Redirect stdout to stderr immediately so print() never corrupts the JSON-RPC stream
_json_out = sys.stdout.buffer if hasattr(sys.stdout, 'buffer') else sys.stdout
sys.stdout = sys.stderr

ROOT          = Path(__file__).parent
CODEBASE_ROOT = Path(os.environ.get("PRUNE_CODEBASE_ROOT", ROOT.parent / "Newexpw" / "new" / "experiment"))
PRUNETOOL_DATA = CODEBASE_ROOT / ".prunetool"
PRUNE_LIBRARY  = CODEBASE_ROOT / "prune library"
TOKEN_LOG      = CODEBASE_ROOT / "token_log.jsonl"
USER_FINDER_JS = CODEBASE_ROOT / "llms_prunetoolfinder.js"

TOKEN_ALERT_THRESHOLD = 20_000
_session_tokens = 0
_rt_model_burned: dict = {}

# ── Terminal logging (to stderr — stdout is reserved for JSON-RPC) ─────

GATEWAY_LOG_URL = "http://localhost:8000/api/mcp-log"

def _log(msg: str, tool: str = "", level: str = "info"):
    """Print to stderr AND forward to gateway terminal (fire-and-forget thread)."""
    print(f"[stdio] {msg}", file=sys.stderr, flush=True)
    import threading, urllib.request as _ur, json as _j
    def _send():
        try:
            payload = _j.dumps({"level": level, "msg": msg, "tool": tool}).encode()
            req = _ur.Request(GATEWAY_LOG_URL, data=payload,
                              headers={"Content-Type": "application/json"}, method="POST")
            _ur.urlopen(req, timeout=2)
        except Exception:
            pass
    threading.Thread(target=_send, daemon=True).start()

def _box(title: str, lines: list, tool: str = ""):
    full = title + " | " + "  ".join(lines)
    _log(full, tool=tool)

# ── Helpers ────────────────────────────────────────────────────────────

def _read_json_file(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        return {}

def _load_user_models() -> list:
    try:
        import re
        text = USER_FINDER_JS.read_text(encoding="utf-8")
        text = re.sub(r'(?<!:)//[^\n]*', '', text)
        text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
        m = re.search(r'models\s*:\s*\[(.+?)\]', text, re.DOTALL)
        if not m:
            return []
        items_text = '[' + m.group(1) + ']'
        items_text = re.sub(r'(?<=[{,])\s*(\w+)\s*:', r'"\1":', items_text)
        items_text = re.sub(r',\s*]', ']', items_text)
        items_text = re.sub(r',\s*}', '}', items_text)
        return json.loads(items_text)
    except Exception:
        return []

def _model_threshold(model: str) -> int:
    try:
        for m in _load_user_models():
            if m.get("model") == model:
                goal = int(m.get("dailyTokenGoal") or 0)
                if goal:
                    return goal
    except Exception:
        pass
    return TOKEN_ALERT_THRESHOLD

# ── Tool handlers ──────────────────────────────────────────────────────

async def _session_start(args: dict) -> dict:
    model = args.get("model", "unknown")
    ts    = args.get("timestamp", "")
    result = {"status": "logged_in", "model": model, "message": "Session started."}

    # Check complexity tier — suggest medium model if using complex
    try:
        user_models = _load_user_models()
        calling = next((m for m in user_models if m.get("model") == model), None)
        if calling and calling.get("complexity") == "complex":
            medium = next((m for m in user_models if m.get("complexity") == "medium"), None)
            if medium and medium.get("model") != model:
                suggestion = f"{medium['label']} ({medium['model']})"
                result["handoff_suggestion"] = {
                    "user_message": (
                        f"⚠️ You're using a complex model ({model}). "
                        f"Switch to {suggestion} for efficient token usage on most coding tasks."
                    )
                }
                _log(f"⚠ Complex model detected. Suggested medium: {suggestion}", tool="session_start")
    except Exception:
        pass

    _box("SESSION START", [f"Model:{model}", f"Time:{ts}"], tool="session_start")
    return result

async def _describe_project(args: dict) -> dict:
    _log("describe_project called", tool="describe_project")
    ctx_path = PRUNETOOL_DATA / "terminal_context.md"
    ctx = ctx_path.read_text(encoding="utf-8") if ctx_path.exists() else ""
    skeleton = _read_json_file(PRUNETOOL_DATA / "skeleton.json")
    folder_map = _read_json_file(PRUNETOOL_DATA / "folder_map.json")
    files = skeleton.get("total_files", 0)
    symbols = skeleton.get("total_symbols", 0)
    folders = len(folder_map.get("folders", {}))
    edges = len(folder_map.get("edges", []))
    return {
        "status": "ready",
        "project_root": str(CODEBASE_ROOT),
        "knowledge_summary": {
            "files_indexed": files,
            "symbols_indexed": symbols,
            "folders": folders,
            "import_edges": edges,
        },
        "context": ctx,
    }

async def _report_tokens(args: dict) -> dict:
    global _session_tokens, _rt_model_burned
    model = args.get("model", "unknown")
    inp = int(args.get("input_tokens", 0))
    out = int(args.get("output_tokens", 0))
    cached = int(args.get("cached_input_tokens", 0))
    effective = inp + out - int(cached * 0.9)
    _session_tokens += effective
    _rt_model_burned[model] = _rt_model_burned.get(model, 0) + effective
    threshold = _model_threshold(model)
    _log(f"model:{model}  in:{inp}  out:{out}  session:{_session_tokens}/{threshold}", tool="report_tokens")
    try:
        entry = json.dumps({"ts": __import__("time").time(), "tokens": inp + out,
                            "effective_tokens": effective, "model": model,
                            "input_tokens": inp, "output_tokens": out})
        with open(TOKEN_LOG, "a", encoding="utf-8") as f:
            f.write(entry + "\n")
    except Exception:
        pass
    threshold = _model_threshold(model)
    burned = _rt_model_burned.get(model, _session_tokens)
    if _session_tokens >= 100_000:
        _session_tokens = 0
        return {
            "session_tokens": burned, "threshold": threshold, "remaining": threshold,
            "SAVE_NOW": True,
            "user_message": (
                "\n⚠️  Token threshold reached — context may be lost soon.\n\n"
                "Please do two things:\n"
                "  1. Type 'save docs'\n"
                "  2. Click 'Project Scan' in the PruneTool dashboard\n"
            ),
            "instruction": "STOP. Print the user_message field above verbatim to the user.",
        }
    # Silent response — no output to user unless threshold hit
    return {"ok": True}

async def _session_end(args: dict) -> dict:
    _log("SESSION END", tool="session_end")
    return {"status": "ended"}

async def _save_docs(args: dict) -> dict:
    try:
        lib = args.get("library_entry", "")
        prog = args.get("progress_update", "")
        if lib:
            lib_path = PRUNE_LIBRARY / "library.md"
            with open(lib_path, "a", encoding="utf-8") as f:
                f.write("\n\n" + lib)
        if prog:
            prog_path = PRUNE_LIBRARY / "PROGRESS.md"
            prog_path.write_text(prog, encoding="utf-8")
        return {"status": "saved"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

async def _analyze_complexity(args: dict) -> dict:
    skeleton = _read_json_file(PRUNETOOL_DATA / "skeleton.json")
    folder_map = _read_json_file(PRUNETOOL_DATA / "folder_map.json")
    symbols = skeleton.get("total_symbols", 0)
    edges = len(folder_map.get("edges", []))
    tier = "simple" if symbols < 1000 and edges < 20 else "complex" if symbols > 5000 or edges > 80 else "medium"
    suggestion = None
    for m in _load_user_models():
        if m.get("complexity") == tier:
            suggestion = m
            break
    return {"complexity_tier": tier, "symbols": symbols, "edges": edges, "suggested_model": suggestion}

HANDLERS = {
    "session_start":     _session_start,
    "describe_project":  _describe_project,
    "report_tokens":     _report_tokens,
    "session_end":       _session_end,
    "save_docs":         _save_docs,
    "analyze_complexity": _analyze_complexity,
}

TOOLS_LIST = [
    {
        "name": "session_start",
        "description": (
            "MANDATORY — Call this FIRST before answering any user message. "
            "Logs your session start. Pass your exact model ID and current timestamp. "
            "Do NOT call describe_project automatically — only when the user explicitly asks."
        ),
        "inputSchema": {"type": "object", "properties": {"model": {"type": "string"}, "timestamp": {"type": "string"}}, "required": ["model", "timestamp"]},
    },
    {
        "name": "describe_project",
        "description": (
            "Returns full project context: folder map, symbol index, prune library docs. "
            "Only call this when the user explicitly asks for project context or KB."
        ),
        "inputSchema": {"type": "object", "properties": {"model": {"type": "string"}, "timestamp": {"type": "string"}}},
    },
    {
        "name": "report_tokens",
        "description": (
            "MANDATORY — Call this after EVERY response you send to the user, no exceptions. "
            "Pass input_tokens, output_tokens, and your exact model ID. "
            "If the response contains SAVE_NOW=true, print the user_message verbatim and ask user to type '/save docs'."
        ),
        "inputSchema": {"type": "object", "properties": {"input_tokens": {"type": "integer"}, "output_tokens": {"type": "integer"}, "model": {"type": "string"}, "cached_input_tokens": {"type": "integer"}}, "required": ["input_tokens", "output_tokens", "model"]},
    },
    {
        "name": "save_docs",
        "description": (
            "Call this when the user types '/save docs'. "
            "Scroll back through the entire conversation, merge all knowledge into library_entry and progress_update, then call this tool."
        ),
        "inputSchema": {"type": "object", "properties": {"library_entry": {"type": "string"}, "progress_update": {"type": "string"}}},
    },
    {
        "name": "session_end",
        "description": "Call this when the user ends the session or says goodbye. Logs session close.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "analyze_complexity",
        "description": "Analyze project complexity and suggest the right model tier (simple/medium/complex).",
        "inputSchema": {"type": "object", "properties": {}},
    },
]

INITIALIZE_INSTRUCTIONS = (
    "You are connected to PruneTool MCP. These are MANDATORY rules — follow them every session, no exceptions:\n\n"
    "RULE 1 — SESSION START (do this before your first response):\n"
    "  Call 'session_start' with your model ID and current timestamp.\n"
    "  Do NOT call 'describe_project' automatically — only call it when the user explicitly asks.\n\n"
    "RULE 2 — AFTER EVERY RESPONSE (no exceptions):\n"
    "  Call 'report_tokens' with: input_tokens, output_tokens, model.\n"
    "  Do NOT pass user_message. This is required for token tracking and alerting.\n\n"
    "RULE 3 — IF report_tokens RETURNS SAVE_NOW=true:\n"
    "  Print the user_message field verbatim. Do not paraphrase.\n"
    "  Ask the user to type '/save docs' before continuing.\n\n"
    "RULE 4 — WHEN USER TYPES '/save docs':\n"
    "  Scroll back through the entire conversation.\n"
    "  Merge all session knowledge into prune library/library.md and PROGRESS.md.\n"
    "  Call 'save_docs' with the merged content.\n"
)

# ── JSON-RPC dispatch ──────────────────────────────────────────────────

async def _dispatch(method: str, params: dict) -> dict:
    if method == "initialize":
        return {
            "protocolVersion": "2025-03-26",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "prunetool-mcp", "version": "1.1.0"},
            "instructions": INITIALIZE_INSTRUCTIONS,
        }
    if method == "tools/list":
        return {"tools": TOOLS_LIST}
    if method == "tools/call":
        name = params.get("name", "")
        arguments = params.get("arguments", {})
        handler = HANDLERS.get(name)
        if not handler:
            raise ValueError(f"Unknown tool: {name}")
        result = await handler(arguments)
        return {"content": [{"type": "text", "text": json.dumps(result)}]}
    if method in ("resources/list", "prompts/list"):
        return {"resources": [], "prompts": []}
    raise ValueError(f"Method not found: {method}")

async def _handle(req: dict) -> dict | None:
    req_id = req.get("id")
    if req_id is None:
        return None  # notification — no response
    method = req.get("method", "")
    params = req.get("params", {}) or {}
    try:
        result = await _dispatch(method, params)
        return {"jsonrpc": "2.0", "id": req_id, "result": result}
    except ValueError as e:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": str(e)}}
    except Exception as e:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32603, "message": f"Internal error: {e}"}}

async def main():
    loop = asyncio.get_event_loop()
    stdin_bin  = sys.stdin.buffer  if hasattr(sys.stdin,  'buffer') else sys.stdin

    def _read_line():
        return stdin_bin.readline()

    while True:
        raw = await loop.run_in_executor(None, _read_line)
        if not raw:
            await asyncio.sleep(0.05)
            continue
        raw = raw.strip()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        if not raw:
            continue
        try:
            body = json.loads(raw)
        except Exception:
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
            _json_out.write((json.dumps(resp) + "\n").encode("utf-8"))
            _json_out.flush()
            continue
        resp = await _handle(body)
        if resp is not None:
            _json_out.write((json.dumps(resp) + "\n").encode("utf-8"))
            _json_out.flush()

if __name__ == "__main__":
    asyncio.run(main())
