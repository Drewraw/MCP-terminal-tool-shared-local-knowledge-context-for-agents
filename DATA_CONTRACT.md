# Data Contract: Backend → Frontend

This document defines the JSON structure that the FastAPI pruning gateway returns to the React frontend. Adherence to this contract ensures smooth rendering of the TokenGauge and DiffView visualizations.

---

## Complete PruneResult JSON Schema

```json
{
  "pruned_files": [
    {
      "file_path": "src/components/Dashboard.tsx",
      "raw_content": "export interface Props { ... }... [full original file] ...",
      "pruned_content": "export interface Props { ... }... # ... [100 lines pruned] ...",
      "raw_lines": 200,
      "pruned_lines": 85,
      "raw_tokens": 1200,
      "pruned_tokens": 408,
      "kept_symbols": ["Props", "DashboardComponent", "useMetrics"],
      "removed_sections": ["lines 45-120", "lines 155-180"]
    }
  ],
  "stats": {
    "total_raw_tokens": 1200,
    "total_pruned_tokens": 408,
    "total_raw_lines": 200,
    "total_pruned_lines": 85,
    "files_processed": 1,
    "symbols_matched": 3,
    "compression_ratio": 2.94,
    "token_savings_pct": 66.0
  },
  "cache_info": {
    "code_hash": "a1b2c3d4e5f67890",
    "cache_hit_likely": false,
    "system_tokens": 1500,
    "code_tokens": 408,
    "query_tokens": 50,
    "total_tokens": 1958
  },
  "goal_hint_used": "Focus on: React component structure",
  "assembled_prompt": {
    "system": [
      {
        "type": "text",
        "text": "You are an expert software engineer...",
        "cache_control": { "type": "ephemeral" }
      },
      {
        "type": "text",
        "text": "## Relevant Codebase Context\n\n### File: src/components/Dashboard.tsx\n...",
        "cache_control": { "type": "ephemeral" }
      }
    ],
    "messages": [
      {
        "role": "user",
        "content": "How does the Dashboard component work?"
      }
    ]
  },
  "elapsed_ms": 45.2
}
```

---

## Field Descriptions

### `pruned_files` Array

Each file in the pruned context.

| Field | Type | Purpose | Example |
|-------|------|---------|---------|
| `file_path` | string | Workspace-relative path | `"src/components/Dashboard.tsx"` |
| `raw_content` | string | Original full file content | `"export interface Props { ..."` |
| `pruned_content` | string | After pruning (with markers) | `"export interface Props { ...\n    # ... [56 lines pruned] ...\n}"` |
| `raw_lines` | int | Total lines in original | `200` |
| `pruned_lines` | int | Lines after pruning | `85` |
| `raw_tokens` | int | Tokens in original (tiktoken) | `1200` |
| `pruned_tokens` | int | Tokens in pruned output | `408` |
| `kept_symbols` | string[] | Which symbols were matched | `["Props", "Dashboard"]` |
| `removed_sections` | string[] | Line ranges that were pruned | `["lines 45-120", "lines 155-180"]` |

### `stats` Object

Aggregate statistics across all pruned files.

| Field | Type | Purpose | Example |
|-------|------|---------|---------|
| `total_raw_tokens` | int | Sum of all raw tokens | `1200` |
| `total_pruned_tokens` | int | Sum of pruned tokens | `408` |
| `compression_ratio` | float | raw / pruned | `2.94` |
| `token_savings_pct` | float | (raw - pruned) / raw × 100 | `66.0` |
| `files_processed` | int | Number of files pruned | `1` |
| `symbols_matched` | int | Total symbols kept | `3` |

### `cache_info` Object

Native Anthropic prompt cache state.

| Field | Type | Purpose | Example |
|-------|------|---------|---------|
| `code_hash` | string | SHA256 of pruned code blocks | `"a1b2c3d4..."` |
| `cache_hit_likely` | bool | Will code hit Anthropic cache? | `false` |
| `system_tokens` | int | System prompt tokens | `1500` |
| `code_tokens` | int | Pruned code tokens | `408` |
| `query_tokens` | int | User query tokens | `50` |
| `total_tokens` | int | All input tokens to API | `1958` |

---

## React Component Data Binding

### TokenGauge Component

**Input Props:**
```tsx
<TokenGauge 
  savings={result.stats.token_savings_pct}  // 66.0
  compression={result.stats.compression_ratio} // 2.94
/>
```

**Expected behavior:**
- Animated circular SVG gauge
- Green (#3fb950) for 50%+ savings
- Yellow (#d29922) for 25-50%
- Red (#f85149) for <25%
- Smooth animation when value changes

### DiffView Component

**Input Props:**
```tsx
<DiffView 
  file={selectedPrunedFile}  // from pruned_files[0]
  showPruneMarkers={true}
/>
```

**Expected behavior:**
- Left pane: raw_content (full file)
- Right pane: pruned_content (with markers)
- Highlight pruning markers in color
- Show token count badges
- Monospace font with line numbers

### StatsGrid Component

**Input Props:**
```tsx
<StatsGrid 
  stats={result.stats}
  cacheInfo={result.cache_info}
/>
```

**Expected behavior:**
- Stat cards for each metric
- Color-coded values
- Loading skeleton fallback

---

## Usage Example in React App

```jsx
import React, { useState } from 'react'
import TokenGauge from './components/TokenGauge'
import DiffView from './components/DiffView'
import StatsGrid from './components/StatsGrid'

export default function PruningDashboard() {
  const [result, setResult] = useState(null)
  const [selectedFile, setSelectedFile] = useState(null)

  const handlePrune = async (query) => {
    const response = await fetch('/prune', {
      method: 'POST',
      body: JSON.stringify({ user_query: query })
    })
    const data = await response.json()
    setResult(data)
    if (data.pruned_files.length > 0) {
      setSelectedFile(data.pruned_files[0].file_path)
    }
  }

  if (!result) return <div>Enter a query to begin</div>

  const selectedPrunedFile = result.pruned_files.find(
    f => f.file_path === selectedFile
  )

  return (
    <div className="dashboard">
      {/* Main metric visualization */}
      <div className="metrics">
        <TokenGauge savings={result.stats.token_savings_pct} />
        <StatsGrid stats={result.stats} cacheInfo={result.cache_info} />
      </div>

      {/* Side-by-side code view */}
      <DiffView file={selectedPrunedFile} />

      {/* File selector */}
      <div className="file-selector">
        {result.pruned_files.map(f => (
          <button
            key={f.file_path}
            onClick={() => setSelectedFile(f.file_path)}
            className={selectedFile === f.file_path ? 'active' : ''}
          >
            {f.file_path}
            <span>-{((f.raw_tokens - f.pruned_tokens) / f.raw_tokens * 100).toFixed(0)}%</span>
          </button>
        ))}
      </div>
    </div>
  )
}
```

---

## Error Handling

If the pruning fails, the backend returns:

```json
{
  "error": "No matches found for goal",
  "code": "NO_MATCHES",
  "pruned_files": [],
  "stats": {
    "total_raw_tokens": 0,
    "total_pruned_tokens": 0,
    "token_savings_pct": 0
  }
}
```

React should display a fallback message or retry the query with a different goal hint.

---

## Performance Notes

- `raw_content` can be large (up to 100KB). Load it lazily.
- `pruned_content` is always ≤ raw_content size.
- Cache queries on `code_hash` to avoid duplicate work.
- WebSocket sends pruning events in real-time (don't wait for full response).

---

## Version History

- **v1.0** (2026-04-01) — Initial data contract with TSX-aware pruning and cache info
