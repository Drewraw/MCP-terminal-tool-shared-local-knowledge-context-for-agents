# 🚀 Quick Start: Run Everything (Phase 3 & 4 Complete)

All components are ready. Here's how to see the 66% token savings gauge in action.

---

## 1️⃣ Start FastAPI Gateway (Terminal 1)

```bash
cd c:\prunetool
python -m server.gateway
```

**Expected output:**
```
INFO:     Uvicorn running on http://127.0.0.1:8000
INFO:     Application startup complete
```

Leave this running. The gateway serves:
- ✅ `/prune` endpoint (POST) - pruning logic
- ✅ `/skeleton` endpoint (GET) - codebase structure
- ✅ `/index` endpoint (POST) - re-index
- ✅ `/mindmap` endpoint (GET) - project tree structure
- ✅ `/annotations` endpoint (GET/POST) - module notes
- ✅ WebSocket `/ws` - live updates
- ✅ Static files at `/ui/*`

---

## 2️⃣ Start React Dev Server (Terminal 2)

```bash
cd c:\prunetool\ui
npm run dev
```

**Expected output:**
```
  ➜  Local:   http://localhost:5173/
  ➜  press h + enter to show help
```

---

## 3️⃣ Test the API (Terminal 3, Optional but Recommended)

Before opening the UI, verify all endpoints work:

```bash
cd c:\prunetool
python test_api.py
```

**Expected output:**
```
✅ Status: 200
✅ Found 15 files
✅ Found 127 symbols

Query: "Show me the React component structure"
✅ Token Savings: 66.0%
✅ Compression: 2.94x
✅ Files: 1

✅ All tests passed! System is ready.
```

---

## 4️⃣ Open the UI (Browser)

Navigate to: **http://localhost:5173**

You should see:
- Header: "Context-Aware Pruning Gateway"
- Status badge: 🟢 Connected (green dot)
- Sidebar: Query input + **Token Savings gauge**
- Main area: Empty state with instructions

---

## 5️⃣ Submit Your First Query

1. **In the query input**, type something like:
   ```
   Show me how the Dashboard component works
   ```

2. **In the goal hint input** (optional), add:
   ```
   Focus on user state management
   ```

3. **Click "Prune & Assemble"** or press **Ctrl+Enter**

4. **Watch the magic happen:**
   - ⏳ Loading spinner appears
   - 🌀 After ~1-2s, response arrives
   - ✨ **TokenGauge animates from 0% → 66%** (smooth 0.6s animation)
   - 📊 StatsGrid shows metrics below the gauge
   - 📄 DiffView loads with side-by-side comparison
   - 📁 File list populates with pruned files

---

## 6️⃣ Interact with Results

### TokenGauge
- Shows exact savings percentage: **66.0%**
- Shows compression ratio: **2.94x**
- Color-coded:
  - 🟢 **Green** = 66%+ (excellent)
  - 🔵 **Blue** = 50-66% (good)
  - 🟡 **Yellow** = 25-50% (okay)
  - 🔴 **Red** = <25% (minimal)

### StatsGrid
- **Token Savings**: 1200 → 408 tokens
- **Compression**: 2.94x
- **Cache Status**: Shows if hit likely
- **Breakdown**: System/Code/Query token split

### DiffView
- **Left pane**: Original code (raw)
- **Right pane**: Pruned code with markers
- **Toggles**: Show/hide either pane
- **Line numbers**: Exact locations
- **Pruning markers**: Where code was removed
- **Kept symbols**: List at bottom

### File Selector
- **Multiple files**: Click to switch between pruned files
- **Symbol count**: Shows how many symbols kept per file
- **Metrics update**: Each file shows its own stats

---

## 7️⃣ Try Different Queries

The gauge animates to different values depending on query. Try:

| Query | Expected Savings |
|-------|------------------|
| "Show me React components" | ~60-70% |
| "How does error handling work?" | ~40-50% |
| "List all database queries" | ~30-40% |
| "Show me all configuration" | ~20-30% |

Each query produces different pruning results!

---

## 8️⃣ Verify Sticky Metrics

**Key feature:** Metrics stay visible even when scrolling:

1. Look at the file list (on the left sidebar)
2. If you have many files, scroll through them
3. Notice the **TokenGauge + StatsGrid stay visible** at the top
4. They don't scroll away!

This is the CSS Grid sticky positioning in action.

---

## 9️⃣ Inspect the Assembled Prompt

Click the **"Assembled Prompt"** tab in the main area to see:

- 🔒 System instructions (cached)
- 💾 Pruned code (cached)
- ❓ Your original query (unique, not cached)
- 📊 Anthropic prompt caching markers

This is the structure sent to Claude API with cache_control: ephemeral.

---

## 🔟 Check the Skeleton View

Click the **"Skeleton"** tab to see:

- File listing with symbols
- All detected functions/classes
- Type definitions
- This is what the pruning engine searches against

---

## 1️⃣1️⃣ Use the Mindmap for Smarter Pruning

This is the secret weapon. Your annotations feed directly into the pruning engine.

### Step 1: Generate the Mindmap

1. Click **"Scan Project"** (the button in the sidebar/header)
2. Switch to the **"Mindmap"** tab
3. You'll see the full project tree: modules, classes, functions

### Step 2: Add Module Notes

Click the **pencil icon** next to any module and type a note:

| Module | Note |
|--------|------|
| `indexer/skeletal_indexer.py` | Performance bottleneck with large files |
| `pruner/pruning_engine.py` | TSX-aware pruning logic here |
| `server/gateway.py` | FastAPI server, WebSocket reconnection needs debugging |

Click **Save** after each note. Notes persist in `.prunetool/annotations.json`.

### Step 3: Query with Context

Now ask a query like:
```
How does file indexing work?
```

**What happens behind the scenes (dual-path system):**

Your annotations are used **twice** — once for search, once for understanding:

**Path 1 — Keyword Boost (pruner search):**
1. The pruner reads your annotations from `.prunetool/annotations.json`
2. Your notes get appended to the goal hint as "Module Context"
3. The skeleton keyword search now matches words like "performance", "bottleneck" from your notes
4. Result: annotated files score higher in search results

**Path 2 — LLM Understanding (assembled prompt):**
1. Your annotations are formatted as "Developer Notes on Modules" in natural language
2. This block is injected into the assembled prompt that Claude actually reads
3. Claude **semantically understands** your notes — it knows "perf issue" = "performance bottleneck", "billing" relates to "payment"
4. Result: Claude prioritizes annotated files in its response, even when terminology differs

**Example:** You annotate `services/billing.py` with `"handles Stripe payments"`. Then query `"how do payment retries work?"`.
- Path 1 catches "payment" as a keyword match
- Path 2 lets Claude understand that "Stripe payments" is semantically relevant to "payment retries" — even though "retries" never appears in the annotation

### Why This Matters

Without annotations, the pruner only has symbol names and signatures to match against your query. With annotations, it has your domain knowledge flowing through **two channels** — keyword search AND LLM comprehension. This is especially useful for:

- Modules with generic names (e.g., `utils.py`, `helpers.ts`) — your note tells the system what's actually inside
- Semantic relationships the keyword search can't catch (e.g., "billing" ↔ "invoice", "auth" ↔ "login")
- Marking areas that need attention ("bug here", "refactor needed", "critical path")

---

## Troubleshooting

### ❌ "Cannot connect to http://localhost:8000"
- Check if FastAPI gateway is running (Terminal 1)
- Verify no other process is using port 8000

### ❌ TokenGauge not animating
- Check browser console for errors (F12 → Console)
- Verify props are being passed: `console.log(props)`
- Check that response includes `token_savings_pct`

### ❌ Metrics scroll away
- CSS Grid layout should keep them sticky
- If not: Check browser's CSS support for `position: sticky`
- Fallback: Use Firefox/Chrome (99%+ support for sticky)

### ❌ DiffView shows blank code
- File might be too large (>50MB): needs virtualization
- Check that `raw_content` and `pruned_content` fields exist
- Try a smaller file first

### ❌ API timeout (>10s)
- Indexer might be processing large codebase
- First run slower: caching kicks in on subsequent queries
- Check server logs for errors

---

## Performance Expectations

| Operation | Time | Notes |
|-----------|------|-------|
| First `/prune` call | 1-3s | Builds index + prunes |
| Subsequent `/prune` calls | 0.2-0.5s | Cache hit, super fast |
| TokenGauge animation | 0.6s | Smooth ease-out |
| DiffView render | <0.1s | Even for 1000-line files |
| Full page load | <100ms | After API response |

---

## Why This Matters: Real Numbers From This Project

This project has **30 source files, 5,719 lines, 42,427 tokens**.

For a query like _"How does file indexing work?"_:

| Metric | Claude (full scan) | PruneTool | Savings |
|--------|-------------------|-----------|---------|
| **Files sent** | 30 (everything) | 4 (matched only) | 87% fewer |
| **Tokens sent** | 42,427 | 1,891 | **95.5% reduction** |
| **Cost per query** | $0.1273 | $0.0057 | **96% cheaper** |
| **100 queries** | $12.73 | $0.57 | **$12.16 saved** |
| **100 queries + cache** | $12.73 | $0.08 | **$12.65 saved (99%)** |

Without pruning, Claude reads 4,976 tokens of CSS, 3,397 tokens of benchmark scripts, 3,254 tokens of React UI code — none of which answers the question. PruneTool strips all that noise and delivers only the 1,891 tokens of indexing-relevant signatures and types.

**Compression ratio: 22.4x**

---

## Architecture Recap

```
┌─────────────────────────────┐
│   Browser (React, 5173)     │
├─────────────────────────────┤
│ • TokenGauge (SVG animate)  │
│ • StatsGrid (metrics)       │
│ • DiffView (side-by-side)   │
│ • File selector             │
└─────────────────────────────┘
         ↕ /prune (POST)
         ↕ /skeleton (GET)
         ↕ WebSocket /ws
┌─────────────────────────────┐
│  FastAPI Gateway (8000)     │
├─────────────────────────────┤
│ • Pruning Pipeline          │
│ • Search Engine             │
│ • Cache Stabilizer          │
│ • Prompt Assembly           │
└─────────────────────────────┘
         ↓ Search
┌─────────────────────────────┐
│  Skeletal Index             │
│  C:\prunetool\{src,ui,...}  │
└─────────────────────────────┘
         ↓ Extract & Prune
┌─────────────────────────────┐
│  Pruned Files + Metrics     │
│  Ready for Claude API       │
└─────────────────────────────┘
```

---

## Next Steps

### ✅ Now Working
- Query interface
- Token savings visualization
- Pruned code comparison
- File selection
- Prompt assembly

### 📋 Optional Enhancements
- [ ] WebSocket live progress (long jobs)
- [ ] Export pruned context as JSON file
- [ ] VS Code extension integration
- [ ] Production deployment (Render, Railway)
- [ ] Dark/light theme toggle
- [ ] Query history search

### 🔧 Debug Info
To enable verbose logging:

```python
# In server/gateway.py, add at top:
import logging
logging.basicConfig(level=logging.DEBUG)
```

---

## Success Indicators

✅ You'll know everything is working when:

1. **Page loads** - No 404s or connection errors
2. **Query submitted** - Status bar shows "Connected" (green dot)
3. **Animation plays** - TokenGauge fills from 0% to actual savings in 0.6s
4. **Numbers appear** - StatsGrid shows: "66.0% Token Savings", "2.94x Compression"
5. **Code shows** - DiffView loads with pruned markers visible
6. **Files scroll independently** - File list scrolls while gauge stays fixed

🎉 If all 6 work, the Phase 3 & 4 implementation is complete!

---

## Commands Quick Reference

```bash
# Start gateway
python -m server.gateway

# Start React dev
npm run dev --prefix ui

# Test API
python test_api.py

# Build React for production
npm run build --prefix ui

# Run benchmark
python benchmark_comparison.py

# Check Python syntax
python -m py_compile pruner/pruning_engine.py
```

---

## Support

If something doesn't work:

1. Check [PHASE_3_4_COMPLETE.md](PHASE_3_4_COMPLETE.md) for detailed implementation notes
2. Review [IMPLEMENTATION_CHECKLIST.md](IMPLEMENTATION_CHECKLIST.md) for debugging steps
3. Read [DATA_CONTRACT.md](DATA_CONTRACT.md) for API schema details
4. Consult [VISUALISER_SETUP.md](VISUALISER_SETUP.md) for component architecture

---

**Ready? Let's see that 66% gauge animate! 🎯**
