import React, { useEffect, useState } from 'react'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000'

const PERIODS = [
  { key: '1h',    label: 'Last 1 hr' },
  { key: '3h',    label: 'Last 3 hrs' },
  { key: 'today', label: 'Today' },
  { key: '2d',    label: 'Last 2 days' },
  { key: '7d',    label: 'Last 7 days' },
  { key: '30d',   label: 'Last 30 days' },
  { key: 'all',   label: 'All time' },
]

const COMPLEXITY = ['simple', 'medium', 'complex']

function fmt(n) {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(2) + 'M'
  if (n >= 1_000)     return (n / 1_000).toFixed(1) + 'k'
  return String(n)
}

function fmtTime(mins) {
  if (!mins || mins < 1/60) return '—'
  if (mins < 1)  return `${Math.round(mins * 60)}s`
  if (mins < 60) return `${mins.toFixed(0)}m`
  return `${(mins / 60).toFixed(1)}h`
}

export default function ModelUsage({ theme }) {
  const [usage, setUsage]         = useState(null)
  const [config, setConfig]       = useState([])   // models from llms_prunetoolfinder.js
  const [period, setPeriod]       = useState('today')
  const [loading, setLoading]     = useState(true)
  const [saving, setSaving]       = useState(false)
  const [saved, setSaved]         = useState(false)
  const [activeView, setActiveView] = useState('usage')  // 'usage' | 'config'

  const dark   = theme === 'dark'
  const bg     = dark ? '#0d1117' : '#ffffff'
  const card   = dark ? '#161b22' : '#f6f8fa'
  const border = dark ? '#30363d' : '#d0d7de'
  const text   = dark ? '#e6edf3' : '#1f2328'
  const muted  = dark ? '#8b949e' : '#656d76'
  const accent = '#3fb950'
  const input  = dark ? '#21262d' : '#ffffff'

  const loadUsage = (p = period) => {
    setLoading(true)
    fetch(`${API_BASE}/api/model-usage?period=${p}`)
      .then(r => r.json())
      .then(d => { setUsage(d); setLoading(false) })
      .catch(() => setLoading(false))
  }

  const loadConfig = () => {
    fetch(`${API_BASE}/api/llm-config`)
      .then(r => r.json())
      .then(d => setConfig(d.models || []))
      .catch(() => {})
  }

  useEffect(() => { loadUsage(period) }, [period])
  useEffect(() => { loadConfig() }, [])

  const updateModel = (idx, field, value) => {
    setConfig(prev => prev.map((m, i) => i === idx ? { ...m, [field]: value } : m))
  }

  const saveConfig = () => {
    setSaving(true)
    fetch(`${API_BASE}/api/llm-config`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ models: config }),
    })
      .then(r => r.json())
      .then(() => { setSaving(false); setSaved(true); setTimeout(() => setSaved(false), 2000) })
      .catch(() => setSaving(false))
  }

  // Build a map of model→goal from config for usage display
  const goalMap = {}
  config.forEach(m => { if (m.model) goalMap[m.model] = m.dailyTokenGoal || 0 })

  const { models: usageModels = [], total_tokens = 0 } = usage || {}
  const maxTokens = usageModels.length ? usageModels[0].tokens : 1

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', background: bg, color: text }}>

      {/* Top bar */}
      <div style={{
        padding: '12px 24px', borderBottom: `1px solid ${border}`,
        display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 10,
      }}>
        {/* View toggle */}
        <div style={{ display: 'flex', gap: 4 }}>
          {[['usage', 'Usage'], ['config', 'Configure Models']].map(([key, label]) => (
            <button key={key} onClick={() => setActiveView(key)} style={{
              padding: '5px 14px', borderRadius: 6, fontSize: 13, cursor: 'pointer',
              border: `1px solid ${activeView === key ? accent : border}`,
              background: activeView === key ? accent : card,
              color: activeView === key ? '#000' : text,
              fontWeight: activeView === key ? 600 : 400,
            }}>{label}</button>
          ))}
        </div>

        {/* Period filter — only on usage view */}
        {activeView === 'usage' && (
          <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', alignItems: 'center' }}>
            {PERIODS.map(p => (
              <button key={p.key} onClick={() => setPeriod(p.key)} style={{
                padding: '4px 10px', borderRadius: 6, fontSize: 12, cursor: 'pointer',
                border: `1px solid ${period === p.key ? accent : border}`,
                background: period === p.key ? accent : card,
                color: period === p.key ? '#000' : text,
                fontWeight: period === p.key ? 600 : 400,
              }}>{p.label}</button>
            ))}
            <button onClick={() => loadUsage(period)} style={{
              padding: '4px 10px', borderRadius: 6, fontSize: 12, cursor: 'pointer',
              border: `1px solid ${border}`, background: card, color: text,
            }}>↻</button>
          </div>
        )}
      </div>

      {/* ── USAGE VIEW ── */}
      {activeView === 'usage' && (
        <div style={{ flex: 1, overflowY: 'auto', padding: 24 }}>
          <div style={{ color: muted, fontSize: 13, marginBottom: 16 }}>
            {fmt(total_tokens)} total tokens across {usageModels.length} model{usageModels.length !== 1 ? 's' : ''}
          </div>

          {loading ? (
            <div style={{ color: muted, textAlign: 'center', paddingTop: 40 }}>Loading...</div>
          ) : usageModels.length === 0 ? (
            <div style={{
              padding: 48, textAlign: 'center', border: `1px dashed ${border}`,
              borderRadius: 8, color: muted,
            }}>
              <div style={{ fontSize: 32, marginBottom: 12 }}>🤖</div>
              <div>No usage recorded for this period.</div>
              <div style={{ fontSize: 12, marginTop: 8 }}>
                LLMs report model name via the <code>report_tokens</code> MCP tool.
              </div>
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              {usageModels.map((m, i) => {
                const goal    = goalMap[m.model] || 0
                const pct     = Math.round((m.tokens / maxTokens) * 100)
                const goalPct = goal ? Math.min(100, Math.round((m.tokens / goal) * 100)) : 0
                return (
                  <div key={m.model} style={{
                    border: `1px solid ${border}`, borderRadius: 8,
                    padding: '16px 20px', background: card,
                  }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                        <span style={{
                          fontSize: 11, fontWeight: 700, padding: '2px 8px', borderRadius: 12,
                          background: i === 0 ? accent : border,
                          color: i === 0 ? '#000' : muted,
                        }}>#{i + 1}</span>
                        <span style={{ fontWeight: 600, fontSize: 15, fontFamily: 'monospace' }}>{m.model}</span>
                      </div>
                      {goal > 0 && (
                        <span style={{
                          fontSize: 12, padding: '2px 10px', borderRadius: 10,
                          background: goalPct >= 90 ? '#f85149' : goalPct >= 70 ? '#d29922' : accent,
                          color: '#000', fontWeight: 600,
                        }}>
                          {goalPct}% of {fmt(goal)} daily goal
                        </span>
                      )}
                    </div>

                    {/* Token bar vs max */}
                    <div style={{ marginBottom: goal ? 6 : 12 }}>
                      <div style={{ height: 6, borderRadius: 3, background: border, overflow: 'hidden' }}>
                        <div style={{
                          width: `${pct}%`, height: '100%', background: accent,
                          borderRadius: 3, transition: 'width 0.4s ease',
                        }} />
                      </div>
                    </div>

                    {/* Goal progress bar */}
                    {goal > 0 && (
                      <div style={{ marginBottom: 12 }}>
                        <div style={{ fontSize: 11, color: muted, marginBottom: 3 }}>Daily goal progress</div>
                        <div style={{ height: 4, borderRadius: 2, background: border, overflow: 'hidden' }}>
                          <div style={{
                            width: `${goalPct}%`, height: '100%',
                            background: goalPct >= 90 ? '#f85149' : goalPct >= 70 ? '#d29922' : '#1f6feb',
                            borderRadius: 2, transition: 'width 0.4s ease',
                          }} />
                        </div>
                      </div>
                    )}

                    <div style={{ display: 'flex', gap: 28, flexWrap: 'wrap' }}>
                      <Stat label="Effective tokens" value={fmt(m.tokens)} accent={accent} />
                      <Stat label="↑ Input"           value={fmt(m.input_tokens   || 0)} color='#58a6ff' />
                      <Stat label="↓ Output"          value={fmt(m.output_tokens  || 0)} color='#f0883e' />
                      {(m.cached_tokens > 0) && <Stat label="⚡ Cached"  value={fmt(m.cached_tokens)} color='#a371f7' />}
                      <Stat label="Active time"       value={fmtTime(m.active_mins)} />
                      <Stat label="MCP calls"         value={m.calls} />
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}

      {/* ── CONFIG VIEW ── */}
      {activeView === 'config' && (
        <div style={{ flex: 1, overflowY: 'auto', padding: 24 }}>
          <div style={{ color: muted, fontSize: 13, marginBottom: 20 }}>
            Set daily token limits and complexity per model. Changes are saved to <code>llms_prunetoolfinder.js</code>.
          </div>

          {config.length === 0 ? (
            <div style={{
              padding: 40, textAlign: 'center', border: `1px dashed ${border}`,
              borderRadius: 8, color: muted,
            }}>
              <div style={{ fontSize: 28, marginBottom: 12 }}>📋</div>
              <div>No models configured yet.</div>
              <div style={{ fontSize: 12, marginTop: 8 }}>
                Uncomment models in <code>llms_prunetoolfinder.js</code> to get started.
              </div>
            </div>
          ) : (
            <>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginBottom: 20 }}>
                {config.map((m, idx) => (
                  <div key={idx} style={{
                    border: `1px solid ${border}`, borderRadius: 8,
                    padding: '14px 18px', background: card,
                    display: 'grid',
                    gridTemplateColumns: '1fr 160px 160px',
                    gap: 16, alignItems: 'center',
                  }}>
                    {/* Model name + label */}
                    <div>
                      <div style={{ fontWeight: 600, fontSize: 14, fontFamily: 'monospace' }}>{m.model}</div>
                      <div style={{ color: muted, fontSize: 12, marginTop: 2 }}>{m.label}</div>
                    </div>

                    {/* Daily token goal */}
                    <div>
                      <div style={{ fontSize: 11, color: muted, marginBottom: 4 }}>Daily token goal</div>
                      <input
                        type="number"
                        min="0"
                        step="1000"
                        value={m.dailyTokenGoal || 0}
                        onChange={e => updateModel(idx, 'dailyTokenGoal', parseInt(e.target.value) || 0)}
                        style={{
                          width: '100%', padding: '5px 8px', borderRadius: 6,
                          border: `1px solid ${border}`, background: input,
                          color: text, fontSize: 13, boxSizing: 'border-box',
                        }}
                      />
                      <div style={{ fontSize: 10, color: muted, marginTop: 3 }}>0 = disabled</div>
                    </div>

                    {/* Complexity */}
                    <div>
                      <div style={{ fontSize: 11, color: muted, marginBottom: 4 }}>Complexity</div>
                      <select
                        value={m.complexity || 'medium'}
                        onChange={e => updateModel(idx, 'complexity', e.target.value)}
                        style={{
                          width: '100%', padding: '5px 8px', borderRadius: 6,
                          border: `1px solid ${border}`, background: input,
                          color: text, fontSize: 13, cursor: 'pointer',
                        }}
                      >
                        {COMPLEXITY.map(c => (
                          <option key={c} value={c}>{c.charAt(0).toUpperCase() + c.slice(1)}</option>
                        ))}
                      </select>
                    </div>
                  </div>
                ))}
              </div>

              <button
                onClick={saveConfig}
                disabled={saving}
                style={{
                  padding: '8px 24px', borderRadius: 6, fontSize: 14, cursor: 'pointer',
                  border: 'none', background: saved ? '#1f6feb' : accent,
                  color: '#000', fontWeight: 600,
                  opacity: saving ? 0.6 : 1,
                }}
              >
                {saving ? 'Saving...' : saved ? '✓ Saved' : 'Save Changes'}
              </button>
            </>
          )}
        </div>
      )}
    </div>
  )
}

function Stat({ label, value, accent, color }) {
  return (
    <div>
      <div style={{ fontSize: 11, color: '#8b949e', marginBottom: 2 }}>{label}</div>
      <div style={{ fontSize: 15, fontWeight: 600, color: color || accent || 'inherit', fontFamily: 'monospace' }}>
        {value}
      </div>
    </div>
  )
}
