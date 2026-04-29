"""
prunetool_main.py - Single binary entry point
==============================================
This is what PyInstaller packages. Starts:
  - Gateway  on http://localhost:8000  (project scanner + /prune API)
  - Proxy    on http://localhost:8080  (OpenAI-compatible IDE endpoint)

On first run: opens setup screen at http://localhost:8000/#/setup
so the user can paste their API key and pick their project folder.
"""

from __future__ import annotations

import os
import sys
import socket
import subprocess
import threading
import time
import webbrowser
from pathlib import Path
try:
    import urllib.request as _urllib
    import json as _json
except ImportError:
    pass

# ── Resolve paths whether running from source or PyInstaller bundle ──
if getattr(sys, "frozen", False):
    # Inside PyInstaller bundle
    BASE_DIR = Path(sys._MEIPASS)
    BIN_DIR  = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).resolve().parent
    BIN_DIR  = BASE_DIR

PYTHON = sys.executable

# ── Load user config from ~/.prunetool/.env ──────────────────────────
def _load_user_env():
    env_file = Path.home() / ".prunetool" / ".env"
    if not env_file.exists():
        return {}
    env = {}
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    return env


def _apply_user_env():
    env = _load_user_env()
    for k, v in env.items():
        os.environ.setdefault(k, v)


def _is_first_run() -> bool:
    env_file = Path.home() / ".prunetool" / ".env"
    if not env_file.exists():
        return True
    env = _load_user_env()
    has_key = any(k in env for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GROQ_API_KEY"))
    has_root = "PRUNE_CODEBASE_ROOT" in env
    return not (has_key and has_root)


# ── Port helpers ──────────────────────────────────────────────────────
def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def _what_owns_port(port: int) -> str:
    """Best-effort: return a hint about what is likely using a port."""
    known = {
        8000: "a local dev server (Django/Rails/FastAPI default)",
        8080: "a local dev server or HTTP proxy (common default)",
        3000: "a Node/React dev server",
        5000: "Flask default dev server",
    }
    return known.get(port, "another process")


def _check_port_or_exit(port: int, role: str):
    """
    If port is occupied, print a clear human-readable error and exit.
    Never silently crash.
    """
    if not _port_free(port):
        owner = _what_owns_port(port)
        print(f"\n{'-'*60}", flush=True)
        print(f"  ERROR: Port {port} is already in use.", flush=True)
        print(f"     Likely cause : {owner}", flush=True)
        print(f"     PruneTool needs port {port} for the {role}.", flush=True)
        print(f"", flush=True)
        print(f"  Fix options:", flush=True)
        print(f"    1. Stop whatever is running on port {port} and retry.", flush=True)
        print(f"    2. Set a different port in ~/.prunetool/.env:", flush=True)
        if role == "Gateway":
            print(f"         GATEWAY_PORT=8001", flush=True)
        else:
            print(f"         PRUNE_PROXY_PORT=8081", flush=True)
        print(f"{'-'*60}\n", flush=True)
        sys.exit(1)


def _wait_for_port(port: int, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _port_free(port):
            return True
        time.sleep(0.3)
    return False


# ── Stream subprocess output to terminal ─────────────────────────────
def _stream(proc, prefix: str):
    try:
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                print(f"{prefix} {line}", flush=True)
    except Exception:
        pass


# ── Start gateway ─────────────────────────────────────────────────────
def start_gateway() -> subprocess.Popen:
    gateway_script = BASE_DIR / "server" / "gateway.py"
    env = {**os.environ, "PYTHONUTF8": "1"}
    proc = subprocess.Popen(
        [PYTHON, str(gateway_script)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        encoding="utf-8", errors="replace", bufsize=1, env=env,
    )
    threading.Thread(target=_stream, args=(proc, "[gateway]"), daemon=True).start()
    return proc


# ── Start proxy ───────────────────────────────────────────────────────
def start_proxy() -> subprocess.Popen:
    proxy_script = BASE_DIR / "proxy_server.py"
    env = {**os.environ, "PYTHONUTF8": "1"}
    proc = subprocess.Popen(
        [PYTHON, str(proxy_script)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        encoding="utf-8", errors="replace", bufsize=1, env=env,
    )
    threading.Thread(target=_stream, args=(proc, "[proxy]"), daemon=True).start()
    return proc


# ── Auto project scan ────────────────────────────────────────────────
def _skeleton_exists() -> bool:
    """Check if a skeleton index already exists for this project."""
    croot = Path(os.environ.get("PRUNE_CODEBASE_ROOT", Path.cwd()))
    skeleton = croot / ".prunetool" / "skeleton.json"
    if not skeleton.exists():
        return False
    try:
        data = _json.loads(skeleton.read_text(encoding="utf-8"))
        return len(data.get("entries", [])) > 0
    except Exception:
        return False


def _trigger_scan(gateway_port: int) -> bool:
    """
    POST /re-scan to gateway. Returns True if accepted.
    Uses stdlib urllib - no httpx dependency at entry point level.
    """
    try:
        url = f"http://localhost:{gateway_port}/re-scan"
        req = _urllib.Request(url, data=b"{}", method="POST",
                              headers={"Content-Type": "application/json"})
        with _urllib.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"[prunetool] Could not trigger scan: {e}", flush=True)
        return False


def _poll_scan_progress(gateway_port: int, timeout: float = 180.0):
    """
    Poll GET /scan-status and print live progress lines until stage == 'complete'.
    Prints a spinner-style update every 3 seconds so user knows it's running.
    """
    url      = f"http://localhost:{gateway_port}/scan-status"
    deadline = time.time() + timeout
    last_msg = ""
    dots     = 0

    print(f"\n[prunetool] --- Project Scan -----------------------------------", flush=True)

    while time.time() < deadline:
        time.sleep(3)
        try:
            req  = _urllib.Request(url, method="GET")
            with _urllib.urlopen(req, timeout=5) as resp:
                data  = _json.loads(resp.read().decode())
            stage = data.get("stage", "idle")
            msg   = data.get("message", "")
            files = data.get("files_found", 0)
            syms  = data.get("symbols_found", 0)
            ann   = data.get("annotated", 0)
            total = data.get("total_to_annotate", 0)

            # Build a single-line progress string
            if stage == "idle":
                progress = "waiting..."
            elif stage == "loading_library":
                progress = "reading prune library..."
            elif stage == "scanning":
                progress = f"indexing files...  {files} files found"
            elif stage == "building_map":
                progress = f"building folder map...  {files} files, {syms} symbols"
            elif stage == "annotating":
                pct = int(ann / total * 100) if total else 0
                progress = f"annotating files...  {ann}/{total}  ({pct}%)"
            elif stage == "complete":
                progress = f"done - {files} files, {syms} symbols indexed"
            else:
                progress = stage

            if progress != last_msg:
                print(f"[scan]  {progress}", flush=True)
                last_msg = progress

            if stage == "complete":
                print(f"[prunetool] OK: Project scan complete - proxy is ready", flush=True)
                print(f"[prunetool] ------------------------------------------------\n", flush=True)
                return

        except Exception:
            dots += 1
            print(f"[scan]  waiting for gateway{'.' * (dots % 4)}  ", flush=True)

    print(f"[prunetool] WARNING: Scan timed out after {int(timeout)}s - proxy will work but context may be partial", flush=True)


def auto_scan_if_needed(gateway_port: int):
    """
    If no skeleton exists for this project, trigger a scan automatically
    and stream live progress to the terminal. Blocking - proxy starts after.
    """
    if _skeleton_exists():
        croot = os.environ.get("PRUNE_CODEBASE_ROOT", str(Path.cwd()))
        print(f"[prunetool] OK: Existing index found for {croot}", flush=True)
        return

    croot = os.environ.get("PRUNE_CODEBASE_ROOT", str(Path.cwd()))
    print(f"\n[prunetool] No project index found for:", flush=True)
    print(f"            {croot}", flush=True)
    print(f"[prunetool] Running first-time project scan - this takes ~15-60s", flush=True)
    print(f"            (only happens once - future startups are instant)", flush=True)

    if _trigger_scan(gateway_port):
        _poll_scan_progress(gateway_port)
    else:
        print(f"[prunetool] WARNING: Could not trigger scan - start one manually in the dashboard", flush=True)


# ── Banner ────────────────────────────────────────────────────────────
def _banner(first_run: bool):
    print("\n" + "=" * 60, flush=True)
    print("  PruneTool - Local AI Proxy", flush=True)
    print("=" * 60, flush=True)
    if first_run:
        print("  FIRST RUN - opening setup screen...", flush=True)
        print("  Paste your API key + pick your project folder.", flush=True)
        print(f"  Setup  >  http://localhost:8000/#/setup", flush=True)
    else:
        croot = os.environ.get("PRUNE_CODEBASE_ROOT", "not set")
        print(f"  Project  :  {croot}", flush=True)
    print(f"  Proxy    >  http://localhost:8080/v1  (point your IDE here)", flush=True)
    print(f"  Dashboard>  http://localhost:8000", flush=True)
    print("=" * 60 + "\n", flush=True)


# ── Main ──────────────────────────────────────────────────────────────
def main():
    _apply_user_env()
    first_run = _is_first_run()

    gateway_port = int(os.environ.get("GATEWAY_PORT", 8000))
    proxy_port   = int(os.environ.get("PRUNE_PROXY_PORT", 8080))

    # ── Pre-flight port check - fail loudly, never silently ──────────
    _check_port_or_exit(gateway_port, "Gateway (scanner + /prune API)")
    _check_port_or_exit(proxy_port,   "Proxy (IDE endpoint)")

    gateway_proc = start_gateway()
    if not _wait_for_port(gateway_port, timeout=20):
        print(f"[prunetool] ERROR: Gateway started but never bound to port {gateway_port}", flush=True)
        print(f"            Check gateway logs above for the real error.", flush=True)
        gateway_proc.terminate()
        sys.exit(1)

    # ── Auto-scan if no index exists - before proxy starts ───────────
    if not first_run:
        auto_scan_if_needed(gateway_port)

    proxy_proc = start_proxy()
    if not _wait_for_port(proxy_port, timeout=10):
        print(f"[prunetool] ERROR: Proxy started but never bound to port {proxy_port}", flush=True)
        print(f"            Check proxy logs above for the real error.", flush=True)
        proxy_proc.terminate()
        gateway_proc.terminate()
        sys.exit(1)

    _banner(first_run)

    if first_run:
        try:
            webbrowser.open("http://localhost:8000/#/setup")
        except Exception:
            pass

    try:
        gateway_proc.wait()
    except KeyboardInterrupt:
        print("\n[prunetool] Shutting down...", flush=True)
    finally:
        proxy_proc.terminate()
        gateway_proc.terminate()


if __name__ == "__main__":
    main()
