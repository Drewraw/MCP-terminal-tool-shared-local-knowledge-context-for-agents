import React, { useState, useEffect, useRef, useCallback } from 'react'
import TokenGauge from './components/TokenGauge.jsx'
import DiffView from './components/DiffView.jsx'
import PromptView from './components/PromptView.jsx'
import StatsGrid from './components/StatsGrid.jsx'
import MindmapView from './components/MindmapView.jsx'
import KnowledgeGraph from './components/KnowledgeGraph.jsx'
import FolderSelector from './components/FolderSelector.jsx'
import ModelUsage from './components/ModelUsage.jsx'
import PromptAssist from './components/PromptAssist.jsx'

const API_BASE = ''

export default function App() {
  const [query, setQuery] = useState('')
  const [goalHint, setGoalHint] = useState('')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState(null)
  const [activeTab, setActiveTab] = useState('diff')
  const [selectedFile, setSelectedFile] = useState(null)
  const [skeleton, setSkeleton] = useState(null)
  const [mindmap, setMindmap] = useState(null)
  const [mindmapSummary, setMindmapSummary] = useState(null)
  const [history, setHistory] = useState([])
  const [connected, setConnected] = useState(false)
  const [graphData, setGraphData] = useState(null)
  const [activeFolderIds, setActiveFolderIds] = useState(null)
  const [projectAnnotations, setProjectAnnotations] = useState(null)
  const [lastScanTime, setLastScanTime] = useState(null)
  const [theme, setTheme] = useState(() => localStorage.getItem('prunetool-theme') || 'dark')
  const [rescanNeeded, setRescanNeeded] = useState(false)
  const [rescanReason, setRescanReason] = useState('')
  const [burnedStats, setBurnedStats] = useState(null)
  const wsRef = useRef(null)

  // ── Scout-select state ───────────────────────────────────────────────────
  const [scoutData, setScoutData] = useState(null)               // result from /scout-select
  const [selectedFolderFiles, setSelectedFolderFiles] = useState({})  // {folder: Set<filepath>}
  const [builtPrompt, setBuiltPrompt] = useState(null)           // final prompt string after OK
  const [copied, setCopied] = useState(false)

  // Apply theme to document
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem('prunetool-theme', theme)
  }, [theme])

  // WebSocket connection for live updates
  useEffect(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const wsUrl = `${protocol}://${window.location.host}/ws`
    let ws

    function connect() {
      ws = new WebSocket(wsUrl)

      ws.onopen = () => {
        setConnected(true)
        wsRef.current = ws
      }

      ws.onmessage = (event) => {
        const msg = JSON.parse(event.data)
        if (msg.type === 'init') {
          setSkeleton(msg.skeleton)
          if (msg.history) setHistory(msg.history)
        } else if (msg.type === 'skeleton_updated') {
          setSkeleton({ total_symbols: msg.total_symbols, file_count: msg.file_count })
          if (msg.indexed_at) setLastScanTime(msg.indexed_at)
          setRescanNeeded(false)
          setRescanReason('')
        } else if (msg.type === 'rescan_needed') {
          setRescanNeeded(true)
          setRescanReason(msg.reason || 'Prune library was updated')
        }
      }

      ws.onclose = () => {
        setConnected(false)
        wsRef.current = null
        setTimeout(connect, 3000)
      }

      ws.onerror = () => ws.close()
    }

    connect()
    return () => { if (ws) ws.close() }
  }, [])

  // Fetch initial data on page load (skeleton + graph + annotations)
  useEffect(() => {
    // Skeleton + last scan time
    fetch(`${API_BASE}/skeleton`)
      .then(r => r.json())
      .then(data => {
        setSkeleton(data)
        if (data.indexed_at) setLastScanTime(data.indexed_at)
      })
      .catch(() => {})

    // Graph data (Knowledge Graph tab)
    Promise.allSettled([
      fetch(`${API_BASE}/mindmap`).then(r => r.json()),
      fetch(`${API_BASE}/mindmap/summary`).then(r => r.json()),
      fetch(`${API_BASE}/graph`).then(r => r.json()),
    ]).then(([mindmapRes, summaryRes, graphRes]) => {
      if (mindmapRes.status === 'fulfilled') setMindmap(mindmapRes.value)
      if (summaryRes.status === 'fulfilled') setMindmapSummary(summaryRes.value)
      if (graphRes.status === 'fulfilled') setGraphData(graphRes.value)
    })

    // Project annotations (Files tab)
    fetch(`${API_BASE}/annotations`)
      .then(r => r.json())
      .then(data => setProjectAnnotations(data))
      .catch(() => {})
  }, [])


  // Poll Bifrost token metrics every 10s
  useEffect(() => {
    const fetchBurned = () =>
      fetch('/api/burned-stats')
        .then(r => r.ok ? r.json() : null)
        .then(data => { if (data) setBurnedStats(data) })
        .catch(() => {})
    fetchBurned()
    const id = setInterval(fetchBurned, 10_000)
    return () => clearInterval(id)
  }, [])

  // Step 1 — Scout: call /scout-select → populate FolderSelector
  const handleScout = useCallback(async () => {
    if (!query.trim() || loading) return
    setLoading(true)
    setBuiltPrompt(null)
    try {
      const resp = await fetch(`${API_BASE}/scout-select`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_query: query, goal_hint: goalHint || undefined }),
      })
      const data = await resp.json()
      setScoutData(data)

      // Pre-select the scout-recommended folders/files
      const initial = {}
      for (const [folder, entry] of Object.entries(data.selected_folders || {})) {
        initial[folder] = new Set(entry.files || [])
      }
      setSelectedFolderFiles(initial)
      setActiveTab('diff')   // switch to Raw vs Pruned (FolderSelector)

      // Also update knowledge graph highlights
      const folderIds = Object.keys(data.selected_folders || {})
      if (folderIds.length) setActiveFolderIds(folderIds)
    } catch (err) {
      console.error('Scout error:', err)
    } finally {
      setLoading(false)
    }
  }, [query, goalHint, loading])

  // Step 2 — OK: build the prompt string from query + selected file paths
  const handleOK = useCallback(() => {
    // Fix 1: filter .md files that come from the root folder
    const allFiles = Object.entries(selectedFolderFiles)
      .flatMap(([folder, files]) => {
        const isRoot = folder === '' || folder === '/' || folder === '(root)'
        return Array.from(files).filter(f => !(isRoot && f.toLowerCase().endsWith('.md')))
      })
      .sort()
    if (!allFiles.length) return

    // Fix 2+3: group files by their immediate parent folder (module grouping)
    const grouped = {}
    for (const f of allFiles) {
      const lastSlash = f.lastIndexOf('/')
      const dir = lastSlash >= 0 ? f.slice(0, lastSlash) : '(root)'
      const name = lastSlash >= 0 ? f.slice(lastSlash + 1) : f
      if (!grouped[dir]) grouped[dir] = []
      grouped[dir].push(name)
    }

    const fileLines = []
    for (const dir of Object.keys(grouped).sort()) {
      const names = grouped[dir]
      if (names.length === 1) {
        // Single file — flat reference
        fileLines.push(`- \`${dir === '(root)' ? names[0] : `${dir}/${names[0]}`}\``)
      } else {
        // Multiple files from same folder — show as module
        fileLines.push(``)
        fileLines.push(`${dir}/  (${names.length} files)`)
        names.forEach(n => fileLines.push(`  - ${n}`))
      }
    }

    const prompt = [
      `Answer this question about the codebase:`,
      query,
      ``,
      `Relevant files:`,
      ...fileLines,
    ].join('\n')

    setBuiltPrompt(prompt)
    setActiveTab('prompt')
    setCopied(false)

    setHistory(prev => [...prev.slice(-49), {
      query: query.slice(0, 100),
      files: allFiles.length,
      timestamp: Date.now() / 1000,
    }])
  }, [query, selectedFolderFiles, scoutData])

  const handleReindex = useCallback(async () => {
    setLoading(true)
    try {
      const resp = await fetch(`${API_BASE}/index`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      })
      const data = await resp.json()
      setSkeleton({ total_symbols: data.total_symbols, file_count: data.file_count })
    } catch (err) {
      console.error('Index error:', err)
    } finally {
      setLoading(false)
    }
  }, [])

  const handleRescan = useCallback(async () => {
    setLoading(true)
    try {
      // Re-scan codebase
      const resp = await fetch(`${API_BASE}/re-scan`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      })
      const data = await resp.json()
      setSkeleton({ total_symbols: data.total_symbols, file_count: data.file_count })

      // Fetch mindmap, summary, and graph in parallel (independent requests)
      const [mindmapRes, summaryRes, graphRes] = await Promise.allSettled([
        fetch(`${API_BASE}/mindmap`).then(r => r.json()),
        fetch(`${API_BASE}/mindmap/summary`).then(r => r.json()),
        fetch(`${API_BASE}/graph`).then(r => r.json()),
      ])

      if (mindmapRes.status === 'fulfilled') setMindmap(mindmapRes.value)
      if (summaryRes.status === 'fulfilled') setMindmapSummary(summaryRes.value)
      if (graphRes.status === 'fulfilled') {
        setGraphData(graphRes.value)
        setActiveFolderIds(null)
        setActiveTab('graph')
      }

      // Refresh annotations + auto-switch to Project tab
      fetch(`${API_BASE}/auto-annotations`)
        .then(r => r.json())
        .then(data => {
          setProjectAnnotations(data.by_folder || {})
          setActiveTab('project')
        })
        .catch(() => {})
    } catch (err) {
      console.error('Re-scan error:', err)
    } finally {
      setLoading(false)
    }
  }, [])

  const handleKeyDown = (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
      e.preventDefault()
      handleScout()
    }
  }

  const stats = result?.stats || {}
  const cacheInfo = result?.cache_info || {}

  return (
    <div className="app">
      {/* Header */}
      <div className="header">
        <h1>Context-Aware Pruning Gateway</h1>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <div className="status-badge">
            <span className="status-dot" style={{ background: connected ? '#3fb950' : '#f85149' }} />
            {connected ? 'Connected' : 'Disconnected'}
            {skeleton && (
              <span style={{ marginLeft: 12, color: '#e6edf3', fontSize: 13, fontWeight: 500 }}>
                {skeleton.total_symbols} symbols / {skeleton.file_count} files
              </span>
            )}
          </div>
          {lastScanTime && (
            <div style={{
              fontSize: 13,
              color: '#e6edf3',
              background: 'var(--bg-secondary)',
              border: '1px solid var(--border)',
              borderRadius: 6,
              padding: '4px 12px',
              whiteSpace: 'nowrap',
              fontWeight: 500,
            }}>
              Last scan: {new Date(lastScanTime).toLocaleString()}
            </div>
          )}
          {burnedStats && (
            <div style={{
              fontSize: 11,
              border: `1px solid ${burnedStats.status === 'Connected' ? '#3fb950' : 'var(--border)'}`,
              borderRadius: 6, padding: '3px 10px', whiteSpace: 'nowrap',
              color: burnedStats.status === 'Connected' ? '#3fb950' : 'var(--text-muted)',
              background: 'var(--bg-secondary)',
            }} title="Token usage tracked via Bifrost proxy">
              {burnedStats.status === 'Connected' ? '🟢 Bifrost Online' : '⚫ Bifrost Offline'}
            </div>
          )}
          <button
            className="theme-toggle"
            onClick={() => setTheme(t => t === 'dark' ? 'light' : 'dark')}
            title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
          >
            {theme === 'dark' ? '\u2600' : '\u263D'}
          </button>
        </div>
      </div>

      <div className="main-layout">
        {/* Sidebar */}
        <div className="sidebar">
          {/* Scan Project */}
          <div className="sidebar-section">
            <div style={{ position: 'relative', display: 'inline-flex', flexDirection: 'column', gap: 4 }}>
              <button
                className="btn"
                onClick={() => { handleRescan(); setRescanNeeded(false); setRescanReason('') }}
                disabled={loading}
                title="Scan for new/deleted/renamed files"
                style={{ fontSize: 14, padding: '8px 20px', fontWeight: 600, ...(rescanNeeded ? { borderColor: '#d7ba7d', color: '#d7ba7d' } : {}) }}
              >
                🔍 Scan Project
                {rescanNeeded && (
                  <span style={{
                    marginLeft: 6,
                    background: '#d7ba7d', color: '#1e1e1e',
                    borderRadius: 8, fontSize: 10, fontWeight: 700,
                    padding: '1px 5px', verticalAlign: 'middle',
                  }}>!</span>
                )}
              </button>
              {rescanNeeded && (
                <div style={{
                  position: 'absolute', top: '100%', left: 0, marginTop: 4,
                  background: '#2d2d2d', border: '1px solid #d7ba7d',
                  borderRadius: 6, padding: '6px 10px',
                  fontSize: 11, color: '#d7ba7d', whiteSpace: 'nowrap',
                  zIndex: 100, lineHeight: 1.5,
                }}>
                  ⚠ {rescanReason}
                </div>
              )}
            </div>
          </div>

          {/* Scout Selection Summary */}
          {scoutData && (
            <div className="sidebar-section">
              <h3>Scout Selected</h3>
              <div className="file-list">
                {Object.entries(selectedFolderFiles).map(([folder, files]) => (
                  <div key={folder} style={{ marginBottom: 4 }}>
                    <div className="file-item" style={{ fontWeight: 600 }}>
                      <span>📁 {folder || '(root)'}</span>
                      <span className="symbol-count">{files.size} files</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Cache Info */}
          {result?.cache_info && (
            <div className="sidebar-section">
              <h3>Cache Status</h3>
              <div className="cache-info">
                <div className="cache-row">
                  <span className="cache-key">Cache Hit Likely</span>
                  <span className={cacheInfo.cache_hit_likely ? 'cache-hit' : 'cache-miss'}>
                    {cacheInfo.cache_hit_likely ? 'YES' : 'NO'}
                  </span>
                </div>
                <div className="cache-row">
                  <span className="cache-key">Code Hash</span>
                  <span className="cache-val">{cacheInfo.code_hash || '—'}</span>
                </div>
                <div className="cache-row">
                  <span className="cache-key">System Tokens</span>
                  <span className="cache-val">{cacheInfo.system_tokens?.toLocaleString() || '—'}</span>
                </div>
                <div className="cache-row">
                  <span className="cache-key">Elapsed</span>
                  <span className="cache-val">{result.elapsed_ms}ms</span>
                </div>
              </div>
            </div>
          )}

          {/* History */}
          {history.length > 0 && (
            <div className="sidebar-section">
              <h3>History ({history.length})</h3>
              {history.slice(-5).reverse().map((h, i) => (
                <div key={i} className="history-item">
                  <div className="history-query">{h.query}</div>
                  <div className="history-meta">
                    <span>{h.stats?.token_savings_pct?.toFixed(1)}% saved</span>
                    <span>{h.elapsed_ms}ms</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* 30-min save-docs reminder banner */}

        {/* Content Area */}
        <div className="content-area">
          <div className="tab-bar">
            <button
              className={`tab ${activeTab === 'graph' ? 'active' : ''}`}
              onClick={() => setActiveTab('graph')}
            >
              Knowledge Graph
            </button>
            <button
              className={`tab ${activeTab === 'project' ? 'active' : ''}`}
              onClick={() => {
                setActiveTab('project')
                if (!projectAnnotations) {
                  fetch(`${API_BASE}/auto-annotations`)
                    .then(r => r.json())
                    .then(data => setProjectAnnotations(data.by_folder || {}))
                    .catch(() => {})
                }
              }}
            >
              Project
            </button>
            <button
              className={`tab ${activeTab === 'llm-usage' ? 'active' : ''}`}
              onClick={() => setActiveTab('llm-usage')}
            >
              LLM Usage
            </button>
            <button
              className={`tab ${activeTab === 'prompt-assist' ? 'active' : ''}`}
              onClick={() => setActiveTab('prompt-assist')}
            >
              Prompt Assist
            </button>
          </div>

<div style={{ display: activeTab === 'graph' ? 'flex' : 'none', flexDirection: 'column', flex: 1, overflow: 'hidden', minHeight: 0 }}>
            <KnowledgeGraph graphData={graphData} activeFolderIds={activeFolderIds} theme={theme} />
          </div>

          {activeTab === 'project' && (
            <ProjectTab
              annotations={projectAnnotations}
              theme={theme}
              onSave={(filePath, annotation) =>
                fetch(`${API_BASE}/auto-annotations`, {
                  method: 'POST',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({ file_path: filePath, annotation }),
                }).catch(() => {})
              }
              onRefresh={() => {
                fetch(`${API_BASE}/auto-annotations`)
                  .then(r => r.json())
                  .then(data => setProjectAnnotations(data.by_folder || {}))
                  .catch(() => {})
              }}
            />
          )}

          {activeTab === 'llm-usage' && (
            <ModelUsage theme={theme} />
          )}

          {activeTab === 'prompt-assist' && (
            <PromptAssist theme={theme} />
          )}
        </div>
      </div>
    </div>
  )
}


// ── Built Prompt View ────────────────────────────────────────────────────────
function BuiltPromptView({ prompt, copied, onCopy, theme }) {
  const isDark = theme === 'dark'
  const fileCount = (prompt.match(/^(\s*- |\s+- )/gm) || []).length

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', padding: 20, gap: 16, overflow: 'auto' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <h3 style={{ margin: 0, fontSize: 16, color: isDark ? '#e6edf3' : '#1f2328' }}>Assembled Prompt</h3>
        <button
          onClick={onCopy}
          style={{
            padding: '6px 18px',
            background: copied ? '#238636' : (isDark ? '#21262d' : '#f3f4f6'),
            color: copied ? '#fff' : (isDark ? '#e6edf3' : '#1f2328'),
            border: `1px solid ${isDark ? '#30363d' : '#d0d7de'}`,
            borderRadius: 6, cursor: 'pointer', fontWeight: 600, fontSize: 13, transition: 'all 0.2s',
          }}
        >
          {copied ? '✓ Copied!' : '📋 Copy'}
        </button>
        <span style={{ fontSize: 12, color: isDark ? '#8b949e' : '#656d76' }}>
          {fileCount} file{fileCount !== 1 ? 's' : ''} — paste into Claude Code / Codex
        </span>
      </div>

      {/* Prompt as plain text */}
      <pre style={{
        margin: 0,
        padding: '14px 16px',
        background: isDark ? '#161b22' : '#f6f8fa',
        border: `1px solid ${isDark ? '#30363d' : '#d0d7de'}`,
        borderRadius: 8,
        fontFamily: 'monospace',
        fontSize: 13,
        lineHeight: 1.7,
        color: isDark ? '#e6edf3' : '#1f2328',
        whiteSpace: 'pre-wrap',
        wordBreak: 'break-word',
        flex: 1,
        overflow: 'auto',
      }}>
        {prompt}
      </pre>
    </div>
  )
}


function SkeletonView({ skeleton }) {
  const [searchQ, setSearchQ] = useState('')
  const [results, setResults] = useState(null)

  const handleSearch = async () => {
    if (!searchQ.trim()) return
    try {
      const resp = await fetch(`${API_BASE}/search`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: searchQ, top_k: 30 }),
      })
      const data = await resp.json()
      setResults(data.results)
    } catch {}
  }

  return (
    <div style={{ padding: 16, overflow: 'auto', flex: 1 }}>
      <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
        <input
          className="goal-input"
          style={{ flex: 1, marginTop: 0 }}
          placeholder="Search skeleton: e.g. 'authentication', 'handleRequest'"
          value={searchQ}
          onChange={e => setSearchQ(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && handleSearch()}
        />
        <button className="btn btn-sm" onClick={handleSearch}>Search</button>
      </div>

      {skeleton?.files && !results && (
        <div>
          <h4 style={{ fontSize: 13, color: '#8b949e', marginBottom: 8 }}>
            Indexed Files ({Object.keys(skeleton.files).length})
          </h4>
          {Object.entries(skeleton.files)
            .sort((a, b) => b[1] - a[1])
            .slice(0, 50)
            .map(([path, count]) => (
              <div key={path} className="file-item">
                <span>{path}</span>
                <span className="symbol-count">{count} symbols</span>
              </div>
            ))}
        </div>
      )}

      {results && (
        <div>
          <h4 style={{ fontSize: 13, color: '#8b949e', marginBottom: 8 }}>
            Results ({results.length})
          </h4>
          {results.map((r, i) => (
            <div key={i} style={{
              padding: '8px 12px',
              borderBottom: '1px solid #30363d',
              fontFamily: 'var(--font-mono)',
              fontSize: 12,
            }}>
              <div style={{ color: '#58a6ff' }}>
                [{r.kind}] {r.parent ? `${r.parent}.` : ''}{r.name}
              </div>
              <div style={{ color: '#8b949e', fontSize: 11 }}>
                {r.file_path}:{r.line_start}-{r.line_end}
              </div>
              <div style={{ color: '#484f58', fontSize: 11, marginTop: 2 }}>
                {r.signature?.slice(0, 120)}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}


// ── Project Tab ──────────────────────────────────────────────────────────────
function ProjectTab({ annotations, theme, onSave, onRefresh }) {
  const isDark = theme === 'dark'
  const [expanded, setExpanded] = useState({})
  const [editing, setEditing] = useState({})      // { file_path: draft_text }
  const [saving, setSaving] = useState({})        // { file_path: true }
  const [localAnnotations, setLocalAnnotations] = useState(annotations)

  // Sync when parent refreshes (e.g. after ↻ Refresh)
  useEffect(() => { setLocalAnnotations(annotations) }, [annotations])

  const bg = isDark ? '#0d1117' : '#fff'
  const border = isDark ? '#21262d' : '#d0d7de'
  const folderBg = isDark ? '#161b22' : '#f6f8fa'
  const textColor = isDark ? '#e6edf3' : '#1f2328'
  const mutedColor = isDark ? '#8b949e' : '#656d76'
  const inputBg = isDark ? '#0d1117' : '#fff'

  if (!annotations) {
    return (
      <div style={{ padding: 40, textAlign: 'center', color: mutedColor }}>
        <div style={{ fontSize: 32, marginBottom: 12 }}>📂</div>
        <div>Loading project annotations…</div>
      </div>
    )
  }

  const folders = Object.keys(localAnnotations || {}).sort()

  if (!folders.length) {
    return (
      <div style={{ padding: 40, textAlign: 'center', color: mutedColor }}>
        <div style={{ fontSize: 32, marginBottom: 12 }}>📂</div>
        <div>No annotations yet. Run <strong>Scan Project</strong> to generate them.</div>
      </div>
    )
  }

  const toggleFolder = (folder) =>
    setExpanded(prev => ({ ...prev, [folder]: !prev[folder] }))

  const handleEdit = (filePath, current) =>
    setEditing(prev => ({ ...prev, [filePath]: current }))

  const handleSave = async (filePath) => {
    const text = (editing[filePath] ?? '').trim()
    setSaving(prev => ({ ...prev, [filePath]: true }))
    await onSave(filePath, text)
    // Update local state immediately — no re-fetch needed
    setLocalAnnotations(prev => {
      if (!prev) return prev
      const next = { ...prev }
      for (const folder of Object.keys(next)) {
        const fname = filePath.split('/').pop()
        if (next[folder][fname]?.file_path === filePath) {
          next[folder] = { ...next[folder], [fname]: { file_path: filePath, annotation: text } }
          break
        }
      }
      return next
    })
    setSaving(prev => ({ ...prev, [filePath]: false }))
    setEditing(prev => { const n = { ...prev }; delete n[filePath]; return n })
  }

  const handleCancel = (filePath) =>
    setEditing(prev => { const n = { ...prev }; delete n[filePath]; return n })

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
      {/* Header */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 12,
        padding: '12px 16px',
        borderBottom: `1px solid ${border}`,
        background: folderBg,
        flexShrink: 0,
      }}>
        <span style={{ fontWeight: 600, color: textColor }}>
          Project Files — {folders.length} folders &nbsp;·&nbsp; {Object.values(localAnnotations || {}).reduce((s, f) => s + Object.keys(f).length, 0)} files
        </span>
        <button
          className="btn btn-sm"
          onClick={onRefresh}
          style={{ marginLeft: 'auto' }}
        >
          ↻ Refresh
        </button>
      </div>

      {/* Folder list */}
      <div style={{ flex: 1, overflow: 'auto', padding: '8px 0' }}>
        {folders.map(folder => {
          const files = localAnnotations[folder]
          const fileNames = Object.keys(files).sort()
          const isOpen = expanded[folder] === true  // default closed

          return (
            <div key={folder} style={{ borderBottom: `1px solid ${border}` }}>
              {/* Folder header */}
              <div
                onClick={() => toggleFolder(folder)}
                style={{
                  display: 'flex', alignItems: 'center', gap: 8,
                  padding: '8px 16px',
                  background: folderBg,
                  cursor: 'pointer',
                  userSelect: 'none',
                }}
              >
                <span style={{ fontSize: 12, color: mutedColor, width: 14 }}>
                  {isOpen ? '▾' : '▸'}
                </span>
                <span style={{ fontSize: 13, fontWeight: 600, color: isDark ? '#79c0ff' : '#0969da' }}>
                  📁 {folder}
                </span>
                <span style={{
                  fontSize: 11, color: isDark ? '#58a6ff' : '#0969da',
                  background: isDark ? 'rgba(88,166,255,0.1)' : 'rgba(9,105,218,0.08)',
                  border: `1px solid ${isDark ? 'rgba(88,166,255,0.2)' : 'rgba(9,105,218,0.2)'}`,
                  borderRadius: 10, padding: '1px 7px', marginLeft: 8,
                }}>
                  {fileNames.length}
                </span>
                <span style={{ fontSize: 11, color: mutedColor, marginLeft: 'auto' }}>
                  {isOpen ? 'collapse' : 'expand'}
                </span>
              </div>

              {/* Files */}
              {isOpen && fileNames.map(fname => {
                const entry = files[fname]
                const filePath = entry.file_path
                const annotation = entry.annotation
                const isEditing = filePath in editing
                const draft = editing[filePath] ?? annotation
                const isSaving = saving[filePath]

                return (
                  <div key={filePath} style={{
                    padding: '8px 16px 8px 40px',
                    borderTop: `1px solid ${border}`,
                    background: bg,
                  }}>
                    {/* File name */}
                    <div style={{
                      display: 'flex', alignItems: 'center', gap: 8,
                      marginBottom: isEditing ? 6 : 0,
                    }}>
                      <span style={{ fontSize: 12, color: mutedColor }}>📄</span>
                      <span style={{ fontSize: 12, fontWeight: 500, color: textColor, flex: 1 }}>
                        {fname}
                      </span>
                      {!isEditing && (
                        <button
                          onClick={() => handleEdit(filePath, annotation)}
                          style={{
                            fontSize: 11, padding: '2px 8px',
                            background: 'transparent',
                            color: mutedColor,
                            border: `1px solid ${border}`,
                            borderRadius: 4, cursor: 'pointer',
                          }}
                        >
                          Edit
                        </button>
                      )}
                    </div>

                    {/* Annotation */}
                    {isEditing ? (
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                        <textarea
                          value={draft}
                          onChange={e => setEditing(prev => ({ ...prev, [filePath]: e.target.value }))}
                          rows={3}
                          style={{
                            width: '100%', boxSizing: 'border-box',
                            padding: '6px 8px',
                            background: inputBg,
                            color: textColor,
                            border: `1px solid ${isDark ? '#388bfd' : '#0969da'}`,
                            borderRadius: 4,
                            fontFamily: 'var(--font-sans, system-ui)',
                            fontSize: 12,
                            lineHeight: 1.5,
                            resize: 'vertical',
                          }}
                          autoFocus
                        />
                        <div style={{ display: 'flex', gap: 6 }}>
                          <button
                            className="btn btn-primary"
                            style={{ fontSize: 12, padding: '3px 12px' }}
                            onClick={() => handleSave(filePath)}
                            disabled={isSaving}
                          >
                            {isSaving ? 'Saving…' : 'Save'}
                          </button>
                          <button
                            className="btn btn-sm"
                            style={{ fontSize: 12, padding: '3px 12px' }}
                            onClick={() => handleCancel(filePath)}
                          >
                            Cancel
                          </button>
                        </div>
                      </div>
                    ) : (
                      <div style={{
                        fontSize: 12, color: annotation ? textColor : mutedColor,
                        lineHeight: 1.5, marginTop: 2,
                        fontStyle: annotation ? 'normal' : 'italic',
                      }}>
                        {annotation || 'No annotation yet'}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )
        })}
      </div>
    </div>
  )
}
