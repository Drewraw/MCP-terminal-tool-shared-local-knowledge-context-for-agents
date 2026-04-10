# PruneTool

MCP-based middleware for persistent project context across AI coding agents.

PruneTool keeps codebase knowledge alive across sessions and tools. You can switch between Claude Code, Codex CLI, Gemini CLI, VS Code, and Cursor without re-explaining the project every time.

Inspired by the SWE-Pruner idea: instead of dumping raw files into context repeatedly, PruneTool indexes the codebase, narrows to relevant symbols, and preserves session knowledge in a local library.

## What It Does

- Builds a skeletal code index using Tree-sitter plus regex fallbacks
- Creates a folder dependency graph for the project
- Uses a Scout model to rank relevant symbols before reading full files
- Prunes file content down to the logic most relevant to the current query
- Exposes an MCP server so supported agents can consume shared project context
- Stores long-term notes in `prune library/` so context survives resets and handoffs
- Tracks token usage and can surface model-budget suggestions

## Core Components

```text
Developer Query
  -> PruneTool Gateway
     1. Skeletal index
     2. Folder dependency map
     3. Scout model ranking
     4. On-demand file extraction
     5. Precision pruning
     6. Context assembly
  -> LLM / agent
```

Main runtime pieces:

- `server/gateway.py`: FastAPI gateway for scanning, pruning, graph data, and UI APIs
- `mcp_server.py`: HTTP MCP server
- `mcp_stdio.py`: stdio MCP entry point for CLI clients
- `proxy_server.py`: local proxy for live token metering
- `start_mcp.py`: unified startup script
- `ui/`: React + Vite dashboard

## Supported Clients

- Claude Code
- Codex CLI
- Gemini CLI
- VS Code
- Cursor

PruneTool can auto-register MCP configuration for supported tools during startup, and it also writes a fallback `.mcp.json` file into the target project.

## Quick Start

### Requirements

- Python 3.10+
- Node.js 18+
- A Groq API key if you want Scout-based ranking via Groq

### 1. Install dependencies

Backend:

```bash
pip install -r server/requirements.txt
```

Frontend:

```bash
cd ui
npm install
npm run build
cd ..
```

### 2. Configure environment

Create a `.env` file:

```env
GROQ_API_KEY=your_groq_key_here
PRUNE_CODEBASE_ROOT=/path/to/project

# Optional proxy settings for live token metering
ANTHROPIC_BASE_URL=http://localhost:8090/v1
OPENAI_BASE_URL=http://localhost:8090/v1
GEMINI_API_BASE_URL=http://localhost:8090/v1
```

Notes:

- `PRUNE_CODEBASE_ROOT` is the project PruneTool will index and monitor
- If Groq is not configured, the system falls back to Ollama or keyword-only selection where available

### 3. Start the stack

Before starting PruneTool, decide which project folder you want it to index. This is required.

PruneTool does not index itself by default. It indexes the target codebase pointed to by `PRUNE_CODEBASE_ROOT`, or in some workflows, the folder you launch it from.

Recommended Windows workflow:

1. Open a terminal in the project you want to index
2. Set `PRUNE_CODEBASE_ROOT` to that project path
3. Start PruneTool using the Python inside the `C:\prunetool\.venv`

PowerShell example:

```powershell
$env:PRUNE_CODEBASE_ROOT="C:\path\to\your\project"
C:\prunetool\.venv\Scripts\python.exe C:\prunetool\start_mcp.py
```

Example with your current project:

```powershell
$env:PRUNE_CODEBASE_ROOT="C:\Users\yourname\source\my-app"
C:\prunetool\.venv\Scripts\python.exe C:\prunetool\start_mcp.py
```

If you are already inside the target project folder, this also works:

```bash
cd C:/path/to/your/project
C:/prunetool/.venv/Scripts/python.exe C:/prunetool/start_mcp.py
```

This starts:

- Gateway UI/API on `http://localhost:8000`
- MCP server on `http://localhost:8765/mcp`
- Local proxy on `http://localhost:8090`

Important:

- If `PRUNE_CODEBASE_ROOT` points to the wrong folder, PruneTool will index the wrong codebase
- Users should set the path to the app or repository they actually want their agents to work on
- After startup, open the dashboard and run a project scan for that target folder

### 4. Scan the project

Open `http://localhost:8000` and run a project scan. This builds:

- skeleton index
- folder graph
- cached annotations
- terminal context snapshot

### 5. Use it from your agent

Ask your agent for project context. The MCP server can provide:

- indexed file and symbol summaries
- folder dependency information
- saved knowledge from `prune library/`

At the end of a session, use:

```text
/save docs
```

That updates the persistent library so the next session or next agent starts with the same project history.

## Session Workflow

PruneTool's MCP flow is built around these tools:

- `session_start`: log a session and initialize model tracking
- `describe_project`: return indexed project context on demand
- `report_tokens`: record usage after each response
- `analyze_complexity`: suggest an appropriate model tier
- `save_docs`: persist session knowledge to the prune library
- `session_end`: close the session cleanly

## Dashboard

The dashboard includes:

- Token usage and daily model-budget views
- Folder dependency graph
- Indexed files and pruned output inspection
- Terminal/MCP log visibility

## Project Structure

```text
prunetool/
|-- cache/
|   `-- cache_stabilizer.py
|-- indexer/
|   |-- skeletal_indexer.py
|   |-- folder_mapper.py
|   |-- mindmap_generator.py
|   `-- models.py
|-- pruner/
|   |-- pruning_engine.py
|   |-- scout.py
|   |-- context_loader.py
|   |-- auto_annotator.py
|   `-- storage_manager.py
|-- server/
|   |-- gateway.py
|   |-- requirements.txt
|   `-- user_manager.py
|-- ui/
|   `-- src/
|-- mcp_server.py
|-- mcp_stdio.py
|-- proxy_server.py
`-- start_mcp.py
```

Runtime data for the indexed project is stored inside the target codebase:

```text
<your-project>/
|-- .prunetool/
|   |-- skeleton.json
|   |-- folder_map.json
|   |-- auto_annotations.json
|   `-- terminal_context.md
`-- prune library/
    |-- library.md
    `-- PROGRESS.md
```

## Manual MCP Configuration

If auto-registration does not apply to your client, add MCP manually.

HTTP transport:

```json
{
  "mcpServers": {
    "prunetool": {
      "url": "http://localhost:8765/mcp"
    }
  }
}
```

stdio transport example:

```bash
codex mcp add prunetool -- python /path/to/prunetool/mcp_stdio.py
```

## API Overview

Important gateway endpoints:

- `POST /prune`
- `POST /scout-select`
- `POST /re-scan`
- `POST /search`
- `GET /skeleton`
- `GET /graph`
- `GET /annotations`
- `POST /annotations`
- `GET /api/burned-stats`
- `GET /api/model-usage`
- `POST /api/mcp-log`
- `WS /ws`

## License

MIT
