"""
attach_terminal.py — Mirror & Tunnel: VS Code side connector
=============================================================
Run this in your VS Code terminal to attach to the same PowerShell
session that is visible in the MCP Dashboard terminal.

Usage:
    python attach_terminal.py --port <relay_port>

The relay port is shown in the MCP Dashboard terminal tab banner.
Both VS Code and the dashboard share the same PTY session:
  - All output appears in both places simultaneously
  - Keystrokes from either side go to the same shell
  - Closing this script does NOT kill the shell

Press Ctrl+C to detach (the shell keeps running).
"""

import argparse
import json
import socket
import sys
import threading
import platform

RELAY_HOST = "127.0.0.1"


def _banner(port: int):
    print(f"\r\n\033[36m{'─'*56}\033[0m")
    print(f"\033[1;36m  PruneTool — Mirror Terminal  (port {port})\033[0m")
    print(f"\033[36m  Attached to shared PTY session.\033[0m")
    print(f"\033[36m  Ctrl+C to detach  (shell keeps running).\033[0m")
    print(f"\033[36m{'─'*56}\033[0m\r\n")
    sys.stdout.flush()


# ── Windows ────────────────────────────────────────────────────────────────────

def _run_windows(sock: socket.socket):
    """
    VS Code integrated terminal uses a pipe for stdin — msvcrt.kbhit()
    never fires there. Read from sys.stdin.buffer directly in a thread instead.
    """
    stop = threading.Event()

    def _recv():
        """Socket → stdout: print PTY output."""
        while not stop.is_set():
            try:
                chunk = sock.recv(4096)
                if not chunk:
                    stop.set()
                    break
                sys.stdout.buffer.write(chunk)
                sys.stdout.buffer.flush()
            except (OSError, ConnectionResetError):
                stop.set()
                break

    def _send():
        """stdin → socket: forward keystrokes to PTY."""
        while not stop.is_set():
            try:
                data = sys.stdin.buffer.read1(256)
                if not data:
                    stop.set()
                    break
                sock.sendall(data)
            except (OSError, EOFError):
                stop.set()
                break

    recv_thread = threading.Thread(target=_recv, daemon=True)
    send_thread = threading.Thread(target=_send, daemon=True)
    recv_thread.start()
    send_thread.start()

    try:
        stop.wait()   # block until either thread sets stop
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()


# ── Mac / Linux ────────────────────────────────────────────────────────────────

def _run_unix(sock: socket.socket):
    import tty
    import termios
    import select

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        while True:
            rlist, _, _ = select.select([sock, sys.stdin], [], [], 0.05)
            for r in rlist:
                if r is sock:
                    chunk = sock.recv(4096)
                    if not chunk:
                        return
                    sys.stdout.buffer.write(chunk)
                    sys.stdout.buffer.flush()
                elif r is sys.stdin:
                    data = sys.stdin.buffer.read(1)
                    if data:
                        sock.sendall(data)
    except KeyboardInterrupt:
        pass
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Attach VS Code terminal to PruneTool relay")
    parser.add_argument("--port", type=int, required=True, help="Relay TCP port shown in MCP Dashboard")
    args = parser.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.connect((RELAY_HOST, args.port))
    except ConnectionRefusedError:
        print(f"\n\033[31mError: Could not connect to relay on port {args.port}.\033[0m")
        print("Make sure the MCP Dashboard terminal tab is open and a session is active.")
        sys.exit(1)

    # ── Send terminal size to gateway so PTY resizes to match ─────────
    import shutil
    ts = shutil.get_terminal_size(fallback=(80, 24))
    handshake = json.dumps({"resize": [ts.lines, ts.columns]}) + "\n"
    sock.sendall(handshake.encode("utf-8"))

    _banner(args.port)

    if platform.system() == "Windows":
        _run_windows(sock)
    else:
        _run_unix(sock)

    print("\r\n\033[33m[Detached from relay — shell is still running]\033[0m\r\n")
    sock.close()


if __name__ == "__main__":
    main()
