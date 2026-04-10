"""
proxy_server.py — Anthropic API proxy for token tracking
=========================================================
Routes Claude Code API calls through here so we can log
real token usage from every response.

Usage:
    python proxy_server.py

Then in your terminal before running claude:
    $env:ANTHROPIC_BASE_URL="http://localhost:8090"
    claude
"""

import asyncio
import json
import os
import time
from pathlib import Path
from dotenv import load_dotenv

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse
import uvicorn

load_dotenv(Path(__file__).parent / ".env")

ANTHROPIC_API_URL = "https://api.anthropic.com"

# Read port from ANTHROPIC_BASE_URL in .env if set, else default 8090
_base_url = os.environ.get("ANTHROPIC_BASE_URL", "http://localhost:8090")
try:
    from urllib.parse import urlparse
    PORT = int(urlparse(_base_url).port or 8090)
except Exception:
    PORT = 8090
CODEBASE_ROOT     = Path(os.environ.get("PRUNE_CODEBASE_ROOT", Path.cwd()))
TOKEN_LOG         = CODEBASE_ROOT / ".prunetool" / "token_log.jsonl"

app = FastAPI(title="PruneTool Anthropic Proxy", version="1.0.0")


def _log_tokens(tokens: int, query_hint: str = ""):
    try:
        TOKEN_LOG.parent.mkdir(exist_ok=True)
        entry = json.dumps({
            "ts":     time.time(),
            "tokens": tokens,
            "query":  query_hint[:120],
            "source": "proxy",
        })
        with TOKEN_LOG.open("a", encoding="utf-8") as f:
            f.write(entry + "\n")
        print(f"[proxy] +{tokens:,} tokens logged  (total session via proxy)")
    except Exception as e:
        print(f"[proxy] token log error: {e}")


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD", "PATCH"])
async def proxy(request: Request, path: str):
    # Forward to real Anthropic API
    url = f"{ANTHROPIC_API_URL}/{path}"

    # Copy headers — keep auth, remove host
    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in ("host", "content-length")
    }

    body = await request.body()

    # Check if client wants streaming
    is_stream = False
    try:
        body_json = json.loads(body)
        is_stream = body_json.get("stream", False)
    except Exception:
        body_json = {}

    async with httpx.AsyncClient(timeout=120.0) as client:

        if is_stream:
            # ── Streaming response — parse SSE for token usage ────────
            async def stream_and_log():
                input_tokens  = 0
                output_tokens = 0

                async with client.stream(
                    request.method, url,
                    headers=headers, content=body,
                ) as resp:
                    async for line in resp.aiter_lines():
                        if line.startswith("data: "):
                            data = line[6:]
                            if data.strip() == "[DONE]":
                                yield f"data: [DONE]\n\n".encode()
                                continue
                            try:
                                evt = json.loads(data)
                                # message_start has input tokens
                                if evt.get("type") == "message_start":
                                    u = evt.get("message", {}).get("usage", {})
                                    input_tokens = u.get("input_tokens", 0)
                                # message_delta has output tokens
                                if evt.get("type") == "message_delta":
                                    u = evt.get("usage", {})
                                    output_tokens = u.get("output_tokens", 0)
                            except Exception:
                                pass
                            yield f"data: {data}\n\n".encode()
                        elif line:
                            yield (line + "\n").encode()

                total = input_tokens + output_tokens
                if total > 0:
                    query = body_json.get("messages", [{}])[-1].get("content", "")
                    if isinstance(query, list):
                        query = str(query[0].get("text", ""))
                    _log_tokens(total, query)

            return StreamingResponse(
                stream_and_log(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        else:
            # ── Non-streaming — read full response, extract usage ─────
            resp = await client.request(
                request.method, url,
                headers=headers, content=body,
            )
            try:
                resp_json = resp.json()
                usage = resp_json.get("usage", {})
                total = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
                if total > 0:
                    query = body_json.get("messages", [{}])[-1].get("content", "")
                    if isinstance(query, list):
                        query = str(query[0].get("text", ""))
                    _log_tokens(total, query)
            except Exception:
                pass

            return Response(
                content=resp.content,
                status_code=resp.status_code,
                headers=dict(resp.headers),
            )


if __name__ == "__main__":
    print(f"\n{'='*56}")
    print(f"  PruneTool — Anthropic API Proxy")
    print(f"  Listening on http://localhost:{PORT}")
    print(f"  Token log  → {TOKEN_LOG}")
    print(f"{'='*56}")
    print(f"\n  Before running claude, set:")
    print(f'  $env:ANTHROPIC_BASE_URL="http://localhost:{PORT}"')
    print(f"  claude\n")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
