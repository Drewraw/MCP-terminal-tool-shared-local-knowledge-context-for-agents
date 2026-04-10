# Visualiser Implementation Checklist

This checklist guides you through wiring the enhanced React components to the FastAPI backend.

---

## Phase 1: Backend Preparation ✅ (Already Complete)

- [x] `pruning_engine.py` implements `_prune_file()` with TSX-aware logic
- [x] `_extract_signature_lines()` slices component bodies to signatures
- [x] `_extract_skeleton_view()` provides fallback for empty pruning
- [x] `/prune` endpoint returns PruneResult JSON (matching DATA_CONTRACT.md)
- [x] `/prune` endpoint calculates `stats` (token_savings_pct, compression_ratio, etc.)

---

## Phase 2: Frontend Components ✅ (Already Complete)

- [x] `TokenGauge.jsx`: SVG circular gauge, color coding, animation
- [x] `DiffView.jsx`: Side-by-side raw vs pruned, line numbers, toggles
- [x] `StatsGrid.jsx`: 2-row layout with cache info breakdown
- [x] `global.css`: ~350 lines of visualizer styling
- [x] `DATA_CONTRACT.md`: JSON schema + React prop bindings

---

## Phase 3: App.jsx Integration (📌 Next Step)

### Step 1: Import the Components

```jsx
// ui/src/App.jsx
import TokenGauge from './components/TokenGauge'
import DiffView from './components/DiffView'
import StatsGrid from './components/StatsGrid'
import PromptView from './components/PromptView'
```

### Step 2: Set Up State

```jsx
const [result, setResult] = useState(null)
const [selectedFileIndex, setSelectedFileIndex] = useState(0)
const [loading, setLoading] = useState(false)
```

### Step 3: Implement handlePrune Function

```jsx
const handlePrune = async (userQuery) => {
  setLoading(true)
  try {
    const response = await fetch('http://localhost:8000/prune', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        user_query: userQuery,
        goal_hint: "Preserve type contracts, slice JSX bodies"
      })
    })
    const data = await response.json()
    setResult(data)
    setSelectedFileIndex(0)  // Start with first file
  } catch (error) {
    console.error('Prune failed:', error)
  } finally {
    setLoading(false)
  }
}
```

### Step 4: Render the Visualiser

```jsx
return (
  <div className="container">
    <PromptView onPrune={handlePrune} loading={loading} />
    
    {result && (
      <div className="visualiser">
        {/* 🎯 TokenGauge: The "66% Saved" gauge */}
        <TokenGauge 
          savings={result.stats.token_savings_pct}
          compression={result.stats.compression_ratio}
        />
        
        {/* 📊 StatsGrid: Token metrics + cache info */}
        <StatsGrid 
          stats={result.stats}
          cacheInfo={result.cache_info}
        />
        
        {/* 📄 DiffView: Side-by-side code comparison */}
        {result.pruned_files.length > 0 && (
          <>
            {/* File selector */}
            <div className="file-selector">
              {result.pruned_files.map((file, idx) => (
                <button
                  key={idx}
                  className={selectedFileIndex === idx ? 'active' : ''}
                  onClick={() => setSelectedFileIndex(idx)}
                >
                  {file.file_path.split('/').pop()}
                </button>
              ))}
            </div>
            
            {/* DiffView for selected file */}
            <DiffView file={result.pruned_files[selectedFileIndex]} />
          </>
        )}
      </div>
    )}
  </div>
)
```

---

## Phase 4: CSS Layout Fixes (If Needed)

If the visualiser doesn't appear in the right layout, check `global.css` has:

```css
.visualiser {
  display: grid;
  grid-template-columns: 1fr;
  gap: 2rem;
  padding: 2rem;
  background: var(--bg-secondary);
  border-radius: 8px;
}

.file-selector {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
  margin: 1rem 0;
}

.file-selector button {
  padding: 0.5rem 1rem;
  background: var(--bg-tertiary);
  border: 1px solid var(--border-color);
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.2s ease;
}

.file-selector button.active {
  background: var(--accent-blue);
  border-color: var(--accent-blue);
  color: white;
}
```

---

## Phase 5: Backend Endpoint Setup

### Verify /prune Endpoint

In `server/gateway.py`, ensure this exists:

```python
@app.post('/prune')
async def prune(request: dict):
    """
    Request format:
    {
      "user_query": "How does Dashboard work?",
      "goal_hint": "...",
      "repo_root": "/path/to/repo"  # optional
    }
    """
    try:
        # 1. Search skeleton for matching files
        # 2. Prune each matched file using pruning_engine
        # 3. Build response matching DATA_CONTRACT.md schema
        result = pruning_engine.prune(
            user_query=request['user_query'],
            goal_hint=request.get('goal_hint', ''),
            repo_root=request.get('repo_root', '.')
        )
        
        return {
            "pruned_files": [
                {
                    "file_path": file.path,
                    "raw_tokens": file.raw_tok,
                    "pruned_tokens": file.pruned_tok,
                    "raw_content": file.raw,
                    "pruned_content": file.pruned
                }
                for file in result.files
            ],
            "stats": {
                "token_savings_pct": result.savings_pct,
                "compression_ratio": result.compression,
                "total_raw_tokens": result.total_raw,
                "total_pruned_tokens": result.total_pruned,
                "files_pruned": len(result.files)
            },
            "cache_info": {
                "cache_hit_likely": result.code_hash == cache.last_hash,
                "code_hash": result.code_hash,
                "cache_ttl": 600
            }
        }
    except Exception as e:
        return {"error": str(e)}, 500
```

### Verify CORS is Enabled

```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

---

## Phase 6: Test the Full Pipeline

### Test 1: Backend Responding

```bash
# Terminal 1: Start FastAPI gateway
cd c:\prunetool
python -m server.gateway
# Should see: "Uvicorn running on http://127.0.0.1:8000"
```

### Test 2: Frontend Loading

```bash
# Terminal 2: Start React dev server
cd c:\prunetool\ui
npm run dev
# Should see: "Local: http://localhost:5173"
```

### Test 3: Send Prune Request

```bash
# Terminal 3: Test the endpoint
curl -X POST http://localhost:8000/prune \
  -H "Content-Type: application/json" \
  -d '{"user_query": "Show me React components", "goal_hint": "Focus on Dashboard"}'

# Should see JSON response with pruned_files[], stats, cache_info
```

### Test 4: Type Gauge Animation

1. Open http://localhost:5173 in browser
2. Enter query: "How does Dashboard work?"
3. Click "Prune"
4. Watch for:
   - ⏳ Loading spinner
   - ✨ TokenGauge animates from 0% to 66%
   - 📊 StatsGrid numbers appear
   - 📄 DiffView loads code

---

## Phase 7: Live WebSocket Updates (Optional But Recommended)

For real-time progress on large pruning jobs, add WebSocket:

### Backend Implementation

```python
@app.websocket("/ws/prune/{session_id}")
async def websocket_prune(websocket: WebSocket, session_id: str):
    await websocket.accept()
    try:
        while True:
            message = await websocket.receive_json()
            if message['type'] == 'prune_start':
                # Stream progress events
                for file in prune_files():
                    await websocket.send_json({
                        'type': 'file_pruned',
                        'file': file.path,
                        'progress': f"{current}/{total}"
                    })
                # Send final result
                await websocket.send_json({
                    'type': 'prune_complete',
                    'result': final_result
                })
    except Exception as e:
        await websocket.close(code=1000)
```

### Frontend Implementation

```jsx
useEffect(() => {
  if (!connected) {
    const ws = new WebSocket('ws://localhost:8000/ws/prune/session123')
    
    ws.onmessage = (event) => {
      const msg = JSON.parse(event.data)
      if (msg.type === 'file_pruned') {
        setProgress(msg.progress)
      } else if (msg.type === 'prune_complete') {
        setResult(msg.result)
      }
    }
    
    return () => ws.close()
  }
}, [connected])
```

---

## Phase 8: Deployment Checklist

### Before Going to Production

- [ ] Backend: All `/prune` endpoint tests pass
- [ ] Frontend: All 3 components render without console errors
- [ ] Animation: TokenGauge smooth at 60 FPS (no jank)
- [ ] Data: StatsGrid shows correct token counts
- [ ] Diff: DiffView renderscode correctly with 100+ line files
- [ ] Cache: cache_hit_likely flag updates correctly
- [ ] Error: Empty pruning shows fallback skeleton view instead of blank
- [ ] Performance: Prune request completes in <2 seconds for typical project
- [ ] Responsive: UI works on 1920x1080 and 1366x768 screens

### Browser Testing

- [x] Chrome/Edge (Chromium-based)
- [ ] Firefox
- [ ] Safari (if macOS testing available)

### Stress Testing

```bash
# Test with large codebase
benchmark_comparison.py --repo /path/to/large/repo --queries 10
# Monitor:
#   - Memory usage (should stay < 500MB)
#   - Response time (should be <3s even for 50MB codebases)
#   - UI responsiveness (no freezes)
```

---

## Phase 9: User Documentation

### Create a User Guide

File: `VISUALISER_USER_GUIDE.md`

```markdown
# How to Use the Pruning Visualiser

1. **Start the gateway and UI:**
   ```
   python server/gateway.py
   npm run dev --prefix ui
   ```

2. **Open your browser** to http://localhost:5173

3. **Enter a query** about your codebase:
   - "Show me all database queries"
   - "How does authentication work?"
   - "Where's the payment integration?"

4. **Hit Enter** to prune

5. **Observe the results:**
   - 🎯 **TokenGauge**: Your token savings percentage
   - 📊 **StatsGrid**: Detailed metrics
   - 📄 **DiffView**: Exactly what was kept vs removed

6. **Click files** to see different pruning comparisons

That's it! The 66% savings means your API calls will be faster and cheaper.
```

---

## Phase 10: Monitoring & Debugging

### If TokenGauge Doesn't Animate

1. Check browser console for errors
2. Verify `result.stats.token_savings_pct` is a number
3. Check CSS has `transition: stroke-dashoffset 0.6s ease-out`

### If DiffView Shows Blank Code

1. Check `result.pruned_files` array is populated
2. Verify `raw_content` and `pruned_content` fields exist
3. Check file isn't too large (>50MB might need virtualization)

### If StatsGrid Shows Wrong Numbers

1. Verify backend is calculating compression correctly: `total_raw / total_pruned`
2. Check cache_info is populated: `cache_hit_likely`, `code_hash`
3. Log the response JSON to verify data shape matches DATA_CONTRACT.md

### Performance Debug

```jsx
// Add timing logs in App.jsx
const start = performance.now()
const data = await response.json()
const end = performance.now()
console.log(`Prune took ${end - start}ms`)
// Target: <2000ms for typical 10-50 file projects
```

---

## Summary: What Gets You 90% of the Way There

1. ✅ **Components exist** → TokenGauge, DiffView, StatsGrid
2. ✅ **CSS complete** → ~350 new lines in global.css
3. ✅ **Backend ready** → /prune endpoint with PruneResult JSON
4. 📌 **Wire App.jsx** → handlePrune function + rendering (THIS IS THE MISSING PIECE)
5. 📌 **Test** → Verify gauge animates, stats show, diff loads
6. 📌 **Deploy** → Start processes, hit http://localhost:5173

**Time estimate for Phase 3-5:** 30 minutes if backend is working, 2-3 hours if backend needs debugging.

---

## Next Immediate Action

1. Read [Phase 3: App.jsx Integration](#phase-3-appjsx-integration)
2. Open `ui/src/App.jsx`
3. Copy the code from Steps 1-4
4. Adapt imports/paths to match your project structure
5. Test with `npm run dev` and 'http://localhost:5173`

Then you'll see the 66% savings visualizer in action! 🎯
