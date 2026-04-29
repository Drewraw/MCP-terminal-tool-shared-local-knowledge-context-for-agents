import React, { useEffect, useState } from 'react'

const API_BASE = ''

const MODELS = [
  'PruneTool Balanced',
  'GPT-5',
  'Claude Sonnet 4.6',
  'Gemini 2.5 Pro',
  'Fast Draft',
]

export default function PromptAssist({ theme }) {
  const [status, setStatus] = useState(null)
  const [input, setInput] = useState('')
  const [model, setModel] = useState(MODELS[0])
  const [mode, setMode] = useState('prompt-assist')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState('')
  const [copied, setCopied] = useState(false)

  const dark = theme === 'dark'
  const card = dark ? '#161b22' : '#f6f8fa'
  const border = dark ? '#30363d' : '#d0d7de'
  const text = dark ? '#e6edf3' : '#1f2328'
  const muted = dark ? '#8b949e' : '#656d76'
  const inputBg = dark ? '#0d1117' : '#ffffff'

  const loadStatus = async () => {
    try {
      const r = await fetch(`${API_BASE}/api/prompt-assist/status`)
      const data = await r.json()
      setStatus(data)
    } catch {
      setStatus({ connected: false, cache_warm: false })
    }
  }

  useEffect(() => {
    loadStatus()
  }, [])

  const improvePrompt = async () => {
    if (!input.trim() || loading) return
    setLoading(true)
    setCopied(false)
    setError('')
    try {
      const r = await fetch(`${API_BASE}/api/prompt-assist`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_input: input, model, mode }),
      })
      const data = await r.json()
      if (!r.ok) {
        throw new Error(data?.detail || 'Failed to generate prompt')
      }
      setResult(data)
      loadStatus()
    } catch (e) {
      setError(e?.message || 'Failed to generate prompt')
    } finally {
      setLoading(false)
    }
  }

  const copyPrompt = async () => {
    if (!result?.suggested_prompt) return
    try {
      await navigator.clipboard.writeText(result.suggested_prompt)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {}
  }

  const report = result?.generation_report

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', padding: 20, gap: 14, overflow: 'auto' }}>
      <div style={{ border: `1px solid ${border}`, borderRadius: 8, background: card, padding: 14 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
          <div>
            <div style={{ fontSize: 15, fontWeight: 700, color: text }}>PruneTool AI Prompt Assist</div>
            <div style={{ fontSize: 12, color: muted, marginTop: 2 }}>
              {status?.project_root || 'Project not detected'}
            </div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 12, color: muted }}>
            <span>{status?.connected ? 'MCP Connected' : 'MCP Offline'}</span>
            <span>{status?.cache_warm ? 'Cache Warm' : 'Cache Cold'}</span>
            <span>{status?.shared_context_loaded ? 'describe_project Cached' : 'describe_project Not Cached'}</span>
          </div>
        </div>
        {status?.shared_context_loaded && (
          <div style={{ marginTop: 8, fontSize: 12, color: muted }}>
            Shared context source: {status?.shared_context_source || '.prunetool/terminal_context.md'}
          </div>
        )}
      </div>

      <div style={{ border: `1px solid ${border}`, borderRadius: 8, background: card, padding: 14 }}>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 10 }}>
          <div style={{ minWidth: 220 }}>
            <div style={{ fontSize: 12, color: muted, marginBottom: 4 }}>Prompt model</div>
            <select
              value={model}
              onChange={(e) => setModel(e.target.value)}
              style={{ width: '100%', padding: '8px 10px', borderRadius: 6, border: `1px solid ${border}`, background: inputBg, color: text }}
            >
              {MODELS.map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
          </div>
          <div style={{ minWidth: 180 }}>
            <div style={{ fontSize: 12, color: muted, marginBottom: 4 }}>Mode</div>
            <select
              value={mode}
              onChange={(e) => setMode(e.target.value)}
              style={{ width: '100%', padding: '8px 10px', borderRadius: 6, border: `1px solid ${border}`, background: inputBg, color: text }}
            >
              <option value="prompt-assist">Prompt Assist</option>
              <option value="deep-context">Deep Context</option>
            </select>
          </div>
        </div>

        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Type rough request. Example: fix login bug in auth flow"
          style={{
            width: '100%',
            minHeight: 92,
            padding: 12,
            borderRadius: 8,
            border: `1px solid ${border}`,
            background: inputBg,
            color: text,
            resize: 'vertical',
            fontSize: 13,
          }}
        />
        <div style={{ marginTop: 10, display: 'flex', gap: 10 }}>
          <button
            className="btn btn-primary"
            onClick={improvePrompt}
            disabled={loading || !input.trim()}
          >
            {loading ? 'Generating...' : 'Improve Prompt'}
          </button>
          <button className="btn" onClick={loadStatus}>Refresh Context</button>
        </div>
        {error && <div style={{ marginTop: 8, color: '#f85149', fontSize: 12 }}>{error}</div>}
      </div>

      {result && (
        <div style={{ border: `1px solid ${border}`, borderRadius: 8, background: card, padding: 14, display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div style={{ fontSize: 12, color: muted }}>
            Intent: {result.intent} | Generated with: {result.model_used}
          </div>
          <div style={{ fontSize: 13, color: text, lineHeight: 1.6, whiteSpace: 'pre-wrap', background: inputBg, border: `1px solid ${border}`, borderRadius: 8, padding: 12 }}>
            {result.suggested_prompt}
          </div>
          <div style={{ display: 'flex', gap: 10 }}>
            <button className="btn btn-primary" onClick={copyPrompt}>{copied ? 'Copied' : 'Copy Prompt'}</button>
          </div>

          {report && (
            <div style={{ border: `1px solid ${border}`, borderRadius: 8, padding: 12, background: inputBg }}>
              <div style={{ fontSize: 12, fontWeight: 700, color: text, marginBottom: 8 }}>Generation report</div>
              <div style={{ fontSize: 12, color: text, lineHeight: 1.6 }}>
                <div>Preset: {report.selected_preset}</div>
                <div>Mode: {report.mode}</div>
                <div>Backend LLM calls: {report.backend_llm_calls}</div>
                <div>Backend models used: {report.backend_models_used.length ? report.backend_models_used.join(', ') : 'none'}</div>
                <div>Actual LLM tokens: {report.actual_llm_tokens}</div>
                <div>Estimated prompt tokens: {report.estimated_prompt_tokens}</div>
                <div>Estimated output tokens: {report.estimated_output_tokens}</div>
                <div>Estimated total tokens: {report.estimated_total_tokens}</div>
                <div style={{ marginTop: 6, color: muted }}>{report.note}</div>
              </div>
            </div>
          )}

          {Array.isArray(result.relevant_context) && result.relevant_context.length > 0 && (
            <div>
              <div style={{ fontSize: 12, fontWeight: 600, color: muted, marginBottom: 5 }}>Relevant context</div>
              {result.relevant_context.map((line, i) => (
                <div key={i} style={{ fontSize: 12, color: text, marginBottom: 3 }}>{line}</div>
              ))}
            </div>
          )}

          {result.shared_context_loaded && result.shared_context_excerpt && (
            <div>
              <div style={{ fontSize: 12, fontWeight: 600, color: muted, marginBottom: 5 }}>
                Shared describe_project cache
              </div>
              <div style={{ fontSize: 12, color: text, lineHeight: 1.5 }}>
                {result.shared_context_excerpt}
              </div>
            </div>
          )}

          {Array.isArray(result.recent_notes) && result.recent_notes.length > 0 && (
            <div>
              <div style={{ fontSize: 12, fontWeight: 600, color: muted, marginBottom: 5 }}>Recent memory notes</div>
              {result.recent_notes.map((line, i) => (
                <div key={i} style={{ fontSize: 12, color: text, marginBottom: 3 }}>{line}</div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
