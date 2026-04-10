"""
start_mcp.py — Single command to start all PruneTool services
=============================================================
Run from your project root:

    python C:/prunetool/start_mcp.py

Starts both services automatically:
  - Gateway  (port 8000) — project scanner, Scout LLM, React UI
  - MCP server (port 8765) — external LLM interface, token monitor, watchdog

On first run: copies llms_prunetoolfinder.js to your project and asks you to
open it and uncomment the models you use.
"""

import os
import sys
import subprocess
import time
import threading
from pathlib import Path

PRUNETOOL_DIR = Path(__file__).resolve().parent
PROJECT_ROOT  = Path.cwd()

os.environ["PRUNE_CODEBASE_ROOT"] = str(PROJECT_ROOT)
os.environ.setdefault("GATEWAY_URL", "http://localhost:8000")
os.environ.setdefault("PYTHONUTF8", "1")

sys.path.insert(0, str(PRUNETOOL_DIR))

PYTHON = str(PRUNETOOL_DIR / ".venv" / "Scripts" / "python.exe")
if not Path(PYTHON).exists():
    PYTHON = sys.executable
STDIO_SCRIPT = PRUNETOOL_DIR / "mcp_stdio.py"


# ════════════════════════════════════════════════════════════════════
# LLM CONFIG — copy template to project on first run
# ════════════════════════════════════════════════════════════════════

def _ensure_llm_finder():
    """
    Copy llms_prunetoolfinder.js template to user's project on first run.
    If the file exists but has no models configured, remind the user to edit it.
    """
    import shutil
    dest = PROJECT_ROOT / "llms_prunetoolfinder.js"
    src  = PRUNETOOL_DIR / "llms_prunetoolfinder.js"

    if not dest.exists():
        shutil.copy2(src, dest)
        print()
        print("=" * 60)
        print("  PruneTool — Model Configuration")
        print("=" * 60)
        print(f"  Created: {dest}")
        print()
        print("  Open that file and uncomment the models you use.")
        print("  Each model needs a 'complexity' tag: simple / medium / complex")
        print("  PruneTool uses this to suggest which model fits each query.")
        print()
        print("  Then restart:  python C:/prunetool/start_mcp.py")
        print("=" * 60)
        print()
        return

    # File exists — check if any models are configured
    try:
        import re, json as _json
        text = dest.read_text(encoding="utf-8")
        text = re.sub(r'(?<!:)(?<!\\)//[^\n]*', "", text)
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
        text = re.sub(r"^\s*module\.exports\s*=\s*", "", text.strip())
        text = text.rstrip(";").strip()
        text = re.sub(r",\s*([}\]])", r"\1", text)
        text = re.sub(r'(?<=[{,])\s*(\w+)\s*:', lambda m: f' "{m.group(1)}":', text)
        data = _json.loads(text)
        models = data.get("models", [])
        if not models:
            print()
            print("  [PruneTool] llms_prunetoolfinder.js has no models configured.")
            print(f"  Open {dest} and uncomment the models you use.")
            print("  PruneTool uses this to suggest which model fits each query.")
            print()
    except Exception:
        pass   # parse error — mcp_server will report it


# ════════════════════════════════════════════════════════════════════
# TERMINAL DEPENDENCY CHECK
# ════════════════════════════════════════════════════════════════════

# Per-platform packages required by the Knowledge Terminal feature.
# Each entry: (import_name, pip_package, description)
_TERMINAL_DEPS = {
    "win32": [
        ("winpty", "pywinpty", "PTY support for interactive shell (Tab/Arrow keys)"),
    ],
    "darwin": [
        # pty + fcntl are stdlib on macOS — nothing extra needed
    ],
    "linux": [
        # pty + fcntl are stdlib on Linux — nothing extra needed
    ],
}


def _try_import(module_name: str) -> bool:
    """Return True if the module can be imported without error."""
    import importlib
    try:
        importlib.import_module(module_name)
        return True
    except ImportError:
        return False


def _pip_install(pip_package: str) -> bool:
    """
    Install a package into the same Python environment that is running
    start_mcp.py.  Uses PYTHON (venv) so the package lands in the right place.
    Returns True on success.
    """
    print(f"  → pip install {pip_package} ...", flush=True)
    result = subprocess.run(
        [PYTHON, "-m", "pip", "install", "--quiet", pip_package],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        print(f"  ✓ {pip_package} installed.", flush=True)
        return True
    else:
        print(f"  ✗ Failed to install {pip_package}:", flush=True)
        # Show last line of pip's error output — usually the most useful bit
        err_lines = (result.stderr or result.stdout or "").strip().splitlines()
        for line in err_lines[-4:]:
            print(f"      {line}", flush=True)
        return False


def _ensure_terminal_deps():
    """
    Check and auto-install platform-specific dependencies for the
    Knowledge Terminal (the real PTY shell session in the dashboard).

    Windows  → pywinpty   (pip install pywinpty)
    macOS    → pty/fcntl stdlib — nothing to install
    Linux    → pty/fcntl stdlib — nothing to install
    """
    platform = sys.platform                          # "win32" | "darwin" | "linux"
    deps     = _TERMINAL_DEPS.get(platform, [])

    if not deps:
        # Verify stdlib pty is accessible (sanity check on macOS/Linux)
        if platform in ("darwin", "linux"):
            if _try_import("pty") and _try_import("fcntl"):
                print("[PruneTool] Terminal deps : pty + fcntl (stdlib) ✓", flush=True)
            else:
                print(
                    "[PruneTool] WARNING: stdlib 'pty' or 'fcntl' not found — "
                    "terminal may not work. Check your Python installation.",
                    flush=True,
                )
        return

    print(f"[PruneTool] Checking terminal deps for platform: {platform}", flush=True)

    missing = []
    for import_name, pip_package, description in deps:
        if _try_import(import_name):
            print(f"  ✓ {pip_package} ({description})", flush=True)
        else:
            print(f"  ✗ {pip_package} missing  — {description}", flush=True)
            missing.append((import_name, pip_package, description))

    if not missing:
        return

    print(f"\n[PruneTool] Auto-installing {len(missing)} missing package(s)...", flush=True)
    failed = []
    for import_name, pip_package, description in missing:
        ok = _pip_install(pip_package)
        if ok:
            # Confirm the install actually works
            if not _try_import(import_name):
                print(
                    f"  ✗ {pip_package} installed but import still fails — "
                    "check your venv.",
                    flush=True,
                )
                failed.append(pip_package)
        else:
            failed.append(pip_package)

    if failed:
        print(flush=True)
        print("[PruneTool] WARNING: Some packages could not be installed automatically:", flush=True)
        for pkg in failed:
            print(f"  Run manually:  {PYTHON} -m pip install {pkg}", flush=True)
        print(
            "\n  The gateway will still start, but the Terminal tab in the\n"
            "  dashboard will show an error until these are installed.",
            flush=True,
        )
    else:
        print("[PruneTool] All terminal deps ready ✓", flush=True)

    print(flush=True)


# ════════════════════════════════════════════════════════════════════
# GATEWAY + MCP LAUNCHER
# ════════════════════════════════════════════════════════════════════

def _is_port_open(port: int) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _stream_output(proc: subprocess.Popen, prefix: str):
    for line in proc.stdout:
        print(f"{prefix} {line}", end="", flush=True)


def start_gateway() -> subprocess.Popen:
    if _is_port_open(8000):
        print("[PruneTool] Gateway already running on port 8000")
        return None

    print("[PruneTool] Starting gateway on port 8000...")
    proc = subprocess.Popen(
        [PYTHON, "-m", "uvicorn", "server.gateway:app",
         "--port", "8000", "--host", "0.0.0.0", "--log-level", "warning"],
        cwd=str(PRUNETOOL_DIR),
        env={**os.environ, "PYTHONUTF8": "1"},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )
    threading.Thread(
        target=_stream_output, args=(proc, "[gateway]"), daemon=True
    ).start()

    for _ in range(15):
        time.sleep(1)
        if _is_port_open(8000):
            print("[PruneTool] Gateway ready on http://localhost:8000")
            return proc

    print("[PruneTool] WARNING: Gateway did not start in 15s")
    return proc


def _proxy_port() -> int:
    base_url = os.environ.get("ANTHROPIC_BASE_URL", "http://localhost:8090")
    try:
        from urllib.parse import urlparse
        return int(urlparse(base_url).port or 8090)
    except Exception:
        return 8090


def _bifrost_exe() -> str | None:
    """Return path to bifrost binary if installed, else None."""
    import shutil
    return shutil.which("bifrost")


def start_proxy() -> subprocess.Popen:
    port = _proxy_port()

    # If port already open — don't fight it
    if _is_port_open(port):
        print(f"[PruneTool] WARNING: Unable to start Bifrost — port {port} is already in use")
        print(f"[PruneTool] Free port {port} and restart, or change ANTHROPIC_BASE_URL to a different port")
        return None

    # Always use npx bifrost (no install required, always latest version)
    import shutil
    npx = shutil.which("npx")
    if not npx:
        print(f"[PruneTool] WARNING: npx not found — token tracking unavailable")
        print(f"[PruneTool] Install Node.js to enable Bifrost token monitoring")
        return None

    # Phase 1: try cached version (instant, no download)
    print(f"[PruneTool] Starting Bifrost (cached) on port {port}...")
    proc = subprocess.Popen(
        [npx, "--no-install", "@maximhq/bifrost", f"-port={port}", "-log-level=error", "-log-style=json"],
        cwd=str(PRUNETOOL_DIR),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8", errors="replace", bufsize=1,
    )
    threading.Thread(
        target=_stream_output, args=(proc, "[bifrost]"), daemon=True
    ).start()

    for _ in range(15):
        time.sleep(1)
        if _is_port_open(port):
            print(f"[PruneTool] Bifrost ready on http://localhost:{port}")
            _print_env_hint(port)
            return proc
        if proc.poll() is not None:
            break  # process already exited — no point waiting further

    # Check if Phase 1 already failed (corrupt cache or not installed)
    if proc.poll() is not None and proc.returncode != 0:
        # Wipe corrupt bifrost cache so npx downloads a clean copy
        import shutil as _shutil
        bifrost_cache = Path(os.environ.get("LOCALAPPDATA", "")) / "bifrost"
        if bifrost_cache.exists():
            print(f"[PruneTool] Removing corrupt Bifrost cache: {bifrost_cache}")
            _shutil.rmtree(bifrost_cache, ignore_errors=True)

        # Phase 2: fresh download
        print(f"[PruneTool] Downloading fresh Bifrost (one-time)...")
        proc = subprocess.Popen(
            [npx, "-y", "@maximhq/bifrost@latest", f"-port={port}", "-log-level=error", "-log-style=json"],
            cwd=str(PRUNETOOL_DIR),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8", errors="replace", bufsize=1,
        )
        threading.Thread(
            target=_stream_output, args=(proc, "[bifrost]"), daemon=True
        ).start()

        for _ in range(25):
            time.sleep(1)
            if _is_port_open(port):
                print(f"[PruneTool] Bifrost ready on http://localhost:{port}")
                _print_env_hint(port)
                return proc

        print(f"[PruneTool] WARNING: Bifrost did not start in 25s — token tracking unavailable")
        return proc

    print(f"[PruneTool] WARNING: Bifrost did not start in 15s — token tracking unavailable")
    return proc


def _print_env_hint(port: int):
    print()
    print("  ┌──────────────────────────────────────────────────────────────────┐")
    print(f"  │  Bifrost is running on http://localhost:{port}/v1                    │")
    print(f"  │  Point any LLM client at this URL to track token usage:          │")
    print(f"  │                                                                  │")
    print(f"  │  Add to your .env file:                                          │")
    print(f"  │    Claude Code:  ANTHROPIC_BASE_URL=http://localhost:{port}/v1        │")
    print(f"  │    Codex:        OPENAI_BASE_URL=http://localhost:{port}/v1           │")
    print(f"  │    Gemini CLI:   GEMINI_API_BASE_URL=http://localhost:{port}/v1       │")
    print(f"  │                                                                  │")
    print(f"  │  LLM proxy examples:                                             │")
    print(f"  │    OpenAI SDK:  base_url=\"http://localhost:{port}/v1\"               │")
    print(f"  │    LiteLLM:     api_base=\"http://localhost:{port}/v1\"               │")
    print(f"  └──────────────────────────────────────────────────────────────────┘")
    print()


def _auto_register_mcp():
    """
    Register the PruneTool MCP server in every LLM tool that supports MCP
    and is installed on this machine. Silently skips missing tools.
    Idempotent — safe to call on every startup.
    """
    import json as _json

    MCP_URL  = "http://localhost:8765/mcp"
    MCP_NAME = "prunetool"
    home     = Path.home()

    def _read_json(path: Path) -> dict:
        try:
            return _json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except Exception:
            return {}

    def _write_json(path: Path, data: dict) -> bool:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(_json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            return True
        except Exception as exc:
            print(f"[PruneTool]   ✗ Could not write {path}: {exc}")
            return False

    def _register(path: Path, keys: list, entry: dict) -> bool:
        """Drill into nested keys, set entry, write back. Returns True if changed."""
        data = _read_json(path)
        node = data
        for k in keys[:-1]:
            node = node.setdefault(k, {})
        leaf_key = keys[-1]
        if node.get(leaf_key) == entry:
            return False  # already correct
        node[leaf_key] = entry
        return _write_json(path, data)

    registered = []

    # Each entry: (label, config_file, detect_dir, keys, entry)
    # detect_dir  — directory whose existence means the tool is installed
    # keys        — nested key path to set  (list entry handled separately)
    simple_targets = [
        (
            "Claude Code",
            home / ".claude" / "settings.json",
            home / ".claude",
            ["mcpServers", MCP_NAME],
            {"type": "http", "url": MCP_URL},
        ),
        (
            "Cursor",
            home / ".cursor" / "mcp.json",
            home / ".cursor",
            ["mcpServers", MCP_NAME],
            {"type": "http", "url": MCP_URL},
        ),
        (
            "Windsurf",
            home / ".codeium" / "windsurf" / "mcp_config.json",
            home / ".codeium",
            ["mcpServers", MCP_NAME],
            {"type": "http", "url": MCP_URL},
        ),
        (
            "VS Code",
            home / ".vscode" / "mcp.json",
            home / ".vscode",
            ["servers", MCP_NAME],
            {"type": "http", "url": MCP_URL},
        ),
        (
            "Zed",
            home / ".config" / "zed" / "settings.json",
            home / ".config" / "zed",
            ["context_servers", MCP_NAME],
            {"source": "custom", "transport": {"type": "http", "url": MCP_URL}},
        ),
        (
            "Gemini CLI",
            home / ".gemini" / "settings.json",
            home / ".gemini",
            ["mcpServers", MCP_NAME],
            {"url": MCP_URL},
        ),
        (
            "LM Studio",
            home / ".lmstudio" / "mcp.json",
            home / ".lmstudio",
            ["mcpServers", MCP_NAME],
            {"type": "http", "url": MCP_URL},
        ),
        (
            "OpenCode",
            home / ".config" / "opencode" / "config.json",
            home / ".config" / "opencode",
            ["mcp", "servers", MCP_NAME],
            {"type": "local", "command": PYTHON, "args": [str(STDIO_SCRIPT)]},
        ),
    ]

    for label, cfg_path, detect_dir, keys, entry in simple_targets:
        if detect_dir.exists():
            if _register(cfg_path, keys, entry):
                registered.append(label)

    # ── Continue (array-based mcpServers) ────────────────────────────
    if (home / ".continue").exists():
        path = home / ".continue" / "config.json"
        try:
            data = _read_json(path)
            servers = data.setdefault("mcpServers", [])
            if not any(s.get("name") == MCP_NAME for s in servers):
                servers.append({"name": MCP_NAME, "transport": {"type": "http", "url": MCP_URL}})
                if _write_json(path, data):
                    registered.append("Continue")
        except Exception as exc:
            print(f"[PruneTool]   ✗ Continue config error: {exc}")

    # ── Jan (multiple install locations) ────────────────────────────
    for jan_dir in [home / "jan", home / "AppData" / "Roaming" / "jan"]:
        if jan_dir.exists():
            path = jan_dir / "settings" / "settings.json"
            if _register(path, ["mcpServers", MCP_NAME], {"type": "http", "url": MCP_URL}):
                registered.append("Jan")
            break

    # ── AnythingLLM (multiple install locations) ─────────────────────
    for allm_dir in [
        home / "anythingllm-desktop" / "storage",
        home / "AppData" / "Roaming" / "anythingllm-desktop" / "storage",
    ]:
        if allm_dir.exists():
            path = allm_dir / "anythingllm_user_preferences.json"
            if _register(path, ["MCPServers", MCP_NAME], {"url": MCP_URL, "type": "http"}):
                registered.append("AnythingLLM")
            break

    # ── Codex CLI (uses CLI command, not config file) ────────────────
    import shutil, subprocess as _sp
    if shutil.which("codex"):
        try:
            existing = _sp.run(["codex", "mcp", "list"], capture_output=True, text=True, timeout=10)
            if MCP_NAME not in existing.stdout:
                _sp.run(
                    ["codex", "mcp", "add", MCP_NAME, "--", PYTHON, str(STDIO_SCRIPT)],
                    capture_output=True, text=True, timeout=10
                )
                registered.append("Codex CLI")
        except Exception:
            pass

    # ── Universal fallback: always write .mcp.json to project root ───
    # Ollama frontends, LibreChat, Open WebUI, local LLMs, any other
    # MCP-compatible tool can be pointed at this file or the URL directly.
    universal = PROJECT_ROOT / ".mcp.json"
    _write_json(universal, {
        "mcpServers": {
            MCP_NAME: {"type": "http", "url": MCP_URL}
        },
    })

    # ── Summary ──────────────────────────────────────────────────────
    all_labels = [t[0] for t in simple_targets] + ["Continue", "Jan", "AnythingLLM"]
    not_registered = [l for l in all_labels if l not in registered]

    print("[PruneTool] ── MCP Registration ─────────────────────────────")
    if registered:
        print(f"[PruneTool]   ✓ Auto-registered : {', '.join(registered)}")
    else:
        print("[PruneTool]   ✗ No known LLM tools detected for auto-registration.")

    if not_registered:
        print(f"[PruneTool]   ✗ Not detected    : {', '.join(not_registered)}")
        print(f"[PruneTool]")
        print(f"[PruneTool]   To connect any unregistered LLM manually:")
        print(f"[PruneTool]     MCP URL    : {MCP_URL}")
        print(f"[PruneTool]")
        print(f"[PruneTool]   • Codex CLI:")
        print(f"[PruneTool]       codex mcp add prunetool --url {MCP_URL}")
        print(f"[PruneTool]")
        print(f"[PruneTool]   • All others (Cursor, VS Code, Windsurf, etc.):")
        print(f"[PruneTool]     Inside your project, create a file named .mcp.json and add:")
        print(f'[PruneTool]       {{"mcpServers": {{"prunetool": {{"type": "http", "url": "{MCP_URL}"}}}}}}')
    print("[PruneTool] ─────────────────────────────────────────────────")


def start_mcp():
    import uvicorn
    _auto_register_mcp()
    print("[PruneTool] Starting MCP server on port 8765...")
    print()
    uvicorn.run(
        "mcp_server:app",
        host="0.0.0.0", port=8765,
        reload=False, log_level="warning",
        app_dir=str(PRUNETOOL_DIR),
    )


# ════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print(f"[PruneTool] Project : {PROJECT_ROOT}")
    print(f"[PruneTool] Install : {PRUNETOOL_DIR}")

    _ensure_llm_finder()
    _ensure_terminal_deps()

    gateway_proc = start_gateway()
    proxy_proc   = start_proxy()

    try:
        start_mcp()
    except KeyboardInterrupt:
        print("\n[PruneTool] Shutting down...")
    finally:
        if proxy_proc:
            proxy_proc.terminate()
        if gateway_proc:
            gateway_proc.terminate()
            print("[PruneTool] Gateway stopped.")
