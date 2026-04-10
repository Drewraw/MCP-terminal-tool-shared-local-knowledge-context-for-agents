/**
 * LLMTerminal.jsx — Mirror & Tunnel Terminal (multi-tab)
 * ═══════════════════════════════════════════════════════
 * VS Code-style multiple terminal tabs. Each tab:
 *  - Opens its own WebSocket to /ws/terminal
 *  - Gets its own PTY session + TCP relay port
 *  - Can be mirrored in VS Code via attach_terminal.py
 *  - Has its own 30-min soft kill countdown
 */

import React, { useEffect, useRef, useState, useCallback } from 'react'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'

const SESSION_SECS   = 30 * 60
const SCAN_MAX_AGE_MS = 30 * 60 * 1000

function fmtTime(secs) {
  const m = String(Math.floor(Math.max(0, secs) / 60)).padStart(2, '0')
  const s = String(Math.max(0, secs) % 60).padStart(2, '0')
  return `${m}:${s}`
}

function getScanAge(lastScanTime) {
  if (!lastScanTime) return Infinity
  return Date.now() - new Date(lastScanTime).getTime()
}

let _tabIdCounter = 1

// ── Single terminal instance component ─────────────────────────────────────

function TerminalPane({ tabId, active, onTitleChange }) {
  const containerRef = useRef(null)
  const termRef      = useRef(null)
  const fitRef       = useRef(null)
  const wsRef        = useRef(null)
  const timerRef     = useRef(null)
  const mountedRef   = useRef(false)

  const [secsLeft,    setSecsLeft]    = useState(SESSION_SECS)
  const [connected,   setConnected]   = useState(false)
  const [linkSevered, setLinkSevered] = useState(false)
  const [attachCmd,   setAttachCmd]   = useState('')
  const [relayPort,   setRelayPort]   = useState(null)
  const [copied,      setCopied]      = useState(false)
  const [errMsg,      setErrMsg]      = useState(null)
  const [kbFiles,     setKbFiles]     = useState([])
  const [kbExpanded,  setKbExpanded]  = useState(false)
  const [sessionNum,  setSessionNum]  = useState(null)

  // Track secsLeft in a ref for the timer closure
  const secsRef = useRef(SESSION_SECS)

  useEffect(() => {
    if (!containerRef.current || mountedRef.current) return
    mountedRef.current = true

    const term = new Terminal({
      convertEol:  true,
      cursorBlink: true,
      fontFamily:  "'Cascadia Code', 'Fira Code', Consolas, 'Courier New', monospace",
      fontSize:    13,
      lineHeight:  1.2,
      scrollback:  5000,
      theme: {
        background: '#1e1e1e', foreground: '#d4d4d4', cursor: '#aeafad',
        selectionBackground: '#264f78',
        black: '#1e1e1e',   red: '#f44747',    green: '#6a9955',
        yellow: '#d7ba7d',  blue: '#569cd6',   magenta: '#c586c0',
        cyan: '#4ec9b0',    white: '#d4d4d4',
        brightBlack: '#808080', brightRed: '#f44747', brightGreen: '#6a9955',
        brightYellow: '#d7ba7d', brightBlue: '#569cd6', brightMagenta: '#c586c0',
        brightCyan: '#4ec9b0',   brightWhite: '#d4d4d4',
      },
    })
    const fit = new FitAddon()
    term.loadAddon(fit)
    term.open(containerRef.current)
    fit.fit()
    termRef.current = term
    fitRef.current  = fit

    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const ws = new WebSocket(`${proto}://${window.location.host}/ws/terminal`)
    wsRef.current = ws

    ws.onopen = () => setConnected(true)

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data)

        if (msg.type === 'session_info') {
          const remaining = SESSION_SECS - (msg.elapsed || 0)
          secsRef.current = remaining
          setSecsLeft(remaining)
          setAttachCmd(msg.attach_cmd || '')
          setRelayPort(msg.relay_port || null)
          setKbFiles(msg.kb_files || [])
          setSessionNum(msg.session_num || tabId)
          onTitleChange(tabId, msg.relay_port, msg.session_num)

          clearInterval(timerRef.current)
          timerRef.current = setInterval(() => {
            secsRef.current -= 1
            setSecsLeft(secsRef.current)
            if (secsRef.current <= 0) clearInterval(timerRef.current)
          }, 1000)

          ws.send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows }))

        } else if (msg.type === 'output') {
          term.write(msg.data)

        } else if (msg.type === 'link_severed') {
          clearInterval(timerRef.current)
          setSecsLeft(0)
          setLinkSevered(true)
          setConnected(false)
          term.write('\r\n\x1b[33m[Dashboard link severed — shell is still running in VS Code terminal]\x1b[0m\r\n')

        } else if (msg.type === 'error') {
          setErrMsg(msg.message)
          term.write(`\r\n\x1b[31m[ERROR] ${msg.message}\x1b[0m\r\n`)
        }
      } catch {
        term.write(event.data)
      }
    }

    ws.onclose = () => { setConnected(false); clearInterval(timerRef.current) }
    ws.onerror = () => {
      term.write('\r\n\x1b[31m[WebSocket error — is the gateway running?]\x1b[0m\r\n')
    }

    term.onData(data => {
      if (ws.readyState === WebSocket.OPEN)
        ws.send(JSON.stringify({ type: 'input', data }))
    })

    term.onResize(({ cols, rows }) => {
      if (ws.readyState === WebSocket.OPEN)
        ws.send(JSON.stringify({ type: 'resize', cols, rows }))
    })

    const ro = new ResizeObserver(() => { if (active) fit.fit() })
    ro.observe(containerRef.current)

    return () => {
      clearInterval(timerRef.current)
      ro.disconnect()
      ws.close()
      term.dispose()
      termRef.current  = null
      fitRef.current   = null
      wsRef.current    = null
      mountedRef.current = false
    }
  }, [tabId])  // only mount once per tabId

  // Refit when tab becomes active
  useEffect(() => {
    if (active && fitRef.current) {
      setTimeout(() => fitRef.current?.fit(), 30)
    }
  }, [active])

  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(attachCmd)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }, [attachCmd])

  const timerColor = secsLeft <= 60 ? '#f44747' : secsLeft <= 300 ? '#d7ba7d' : '#6a9955'

  return (
    <div style={{
      display: 'flex', flexDirection: 'column', height: '100%',
      overflow: 'hidden', position: 'relative',
    }}>
      {/* Attach command banner */}
      {attachCmd && !linkSevered && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: 10,
          padding: '5px 12px', background: '#252526',
          borderBottom: '1px solid #3c3c3c', flexShrink: 0,
          fontFamily: "'Segoe UI', system-ui, sans-serif", fontSize: 11,
        }}>
          <span style={{ color: '#858585', flexShrink: 0 }}>Mirror in VS Code:</span>
          <code style={{
            flex: 1, color: '#9cdcfe',
            fontFamily: "'Cascadia Code', Consolas, monospace",
            fontSize: 11, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
          }}>{attachCmd}</code>
          <button onClick={handleCopy} style={{
            background: copied ? '#238636' : '#3c3c3c',
            color: '#d4d4d4', border: '1px solid #555',
            borderRadius: 3, padding: '2px 10px', fontSize: 11,
            cursor: 'pointer', flexShrink: 0, transition: 'background 0.2s',
          }}>
            {copied ? '✓ Copied' : 'Copy'}
          </button>
        </div>
      )}

      {/* KB files panel */}
      {kbFiles.length > 0 && !linkSevered && (
        <div style={{ background: '#1a2a1a', borderBottom: '1px solid #2a4a2a', flexShrink: 0 }}>
          <div
            onClick={() => setKbExpanded(x => !x)}
            style={{
              display: 'flex', alignItems: 'center', gap: 8,
              padding: '5px 12px', cursor: 'pointer',
              fontSize: 11, color: '#6a9955',
              fontFamily: "'Segoe UI', system-ui, sans-serif",
            }}
          >
            <span>{kbExpanded ? '▾' : '▸'}</span>
            <span style={{ fontWeight: 600 }}>
              📚 LLM Knowledge Base — {kbFiles.length} files pre-loaded
            </span>
            <span style={{ color: '#3c5a3c', marginLeft: 'auto', fontSize: 10 }}>
              Any LLM running here can read these directly
            </span>
          </div>
          {kbExpanded && (
            <div style={{ padding: '0 12px 8px 28px', display: 'flex', flexDirection: 'column', gap: 2 }}>
              {kbFiles.map((f, i) => {
                const name = f.replace(/\\/g, '/').split('/').slice(-2).join('/')
                const isLib    = f.includes('prune library')
                const isJson   = f.endsWith('.json')
                const isReadme = f.toLowerCase().includes('readme')
                return (
                  <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11,
                    fontFamily: "'Segoe UI', system-ui, sans-serif" }}>
                    <span style={{ fontSize: 10 }}>
                      {isLib ? '📄' : isJson ? '🗂' : isReadme ? '📖' : '📄'}
                    </span>
                    <code style={{
                      color: isLib ? '#4ec9b0' : isJson ? '#9cdcfe' : '#d4d4d4',
                      fontFamily: "'Cascadia Code', Consolas, monospace", fontSize: 11,
                    }}>{name}</code>
                    <span style={{ color: '#3c5a3c', fontSize: 10 }}>
                      {isLib ? '— session memory' : isJson ? '— knowledge graph' : '— project docs'}
                    </span>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}

      {/* xterm.js */}
      <div ref={containerRef} style={{ flex: 1, overflow: 'hidden', padding: '4px 0 0 4px' }} />

      {/* Dashboard Disconnected overlay */}
      {linkSevered && (
        <div style={{
          position: 'absolute', top: 0, left: 0, right: 0,
          background: 'rgba(30,30,30,0.93)',
          borderBottom: '2px solid #f44747',
          padding: '10px 16px',
          display: 'flex', alignItems: 'center', gap: 12,
          fontFamily: "'Segoe UI', system-ui, sans-serif",
          zIndex: 10,
        }}>
          <span style={{ fontSize: 18 }}>🔌</span>
          <div>
            <div style={{ color: '#f44747', fontWeight: 700, fontSize: 13 }}>
              Dashboard Disconnected — 30-minute session ended
            </div>
            <div style={{ color: '#858585', fontSize: 11, marginTop: 2 }}>
              Shell is still running in VS Code terminal.
            </div>
          </div>
        </div>
      )}

      {/* Status bar */}
      <div style={{
        height: 22, flexShrink: 0,
        background: linkSevered ? '#5a1d1d' : connected ? '#007acc' : '#3c3c3c',
        color: '#fff', fontSize: 11,
        display: 'flex', alignItems: 'center',
        padding: '0 12px', gap: 10,
        fontFamily: "'Segoe UI', system-ui, sans-serif",
        transition: 'background 0.3s',
      }}>
        <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{
            width: 7, height: 7, borderRadius: '50%', display: 'inline-block',
            background: connected ? '#6a9955' : linkSevered ? '#f44747' : '#888',
          }} />
          {linkSevered
            ? 'Dashboard link severed — shell alive in VS Code'
            : connected ? '⚡ Mirror & Tunnel Terminal (pywinpty relay)' : 'Connecting…'}
        </span>
        {connected && !linkSevered && (
          <span style={{ marginLeft: 'auto', color: timerColor, fontFamily: "'Cascadia Code', Consolas, monospace" }}>
            {fmtTime(secsLeft)}
          </span>
        )}
        {errMsg && (
          <span style={{ color: '#f44747', marginLeft: 'auto' }}>⚠ {errMsg.slice(0, 80)}</span>
        )}
      </div>
    </div>
  )
}

// ── Multi-tab shell wrapper ─────────────────────────────────────────────────

export default function LLMTerminal({ theme, lastScanTime, skeleton }) {
  const scanFresh = getScanAge(lastScanTime) < SCAN_MAX_AGE_MS

  // tabs: [{ id, label, relayPort, sessionNum, severed }]
  const [tabs,      setTabs]      = useState(() => [{ id: _tabIdCounter++, label: 'shell', relayPort: null, sessionNum: null }])
  const [activeTab, setActiveTab] = useState(tabs[0].id)

  // ALL hooks must be above any early return (Rules of Hooks)
  const handleTitleChange = useCallback((tabId, relayPort, sessionNum) => {
    setTabs(prev => prev.map(t =>
      t.id === tabId
        ? { ...t, relayPort, sessionNum, label: `shell ${sessionNum || ''}`.trim() }
        : t
    ))
  }, [])

  // ── Scan gate ─────────────────────────────────────────────────────────
  if (!scanFresh) {
    const ageMin = lastScanTime ? Math.floor(getScanAge(lastScanTime) / 60000) : null
    return (
      <div style={{
        display: 'flex', flexDirection: 'column', alignItems: 'center',
        justifyContent: 'center', height: '100%',
        background: '#1e1e1e', gap: 18,
        fontFamily: "'Segoe UI', system-ui, sans-serif",
      }}>
        <div style={{ fontSize: 40 }}>🔍</div>
        <div style={{ color: '#d7ba7d', fontSize: 15, fontWeight: 600 }}>
          {!lastScanTime
            ? 'Project has never been scanned'
            : `Knowledge database is ${ageMin} min old (limit: 30 min)`}
        </div>
        <div style={{ color: '#858585', fontSize: 13, textAlign: 'center', maxWidth: 400, lineHeight: 1.7 }}>
          Click <strong style={{ color: '#4ec9b0' }}>Scan Project</strong> in the sidebar
          to update the knowledge database.
          <br />The terminal will connect automatically once the scan completes.
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: '#555', fontSize: 12, marginTop: 8 }}>
          <span style={{ width: 8, height: 8, borderRadius: '50%', background: '#555', display: 'inline-block' }} />
          Waiting for scan to complete…
        </div>
        <div style={{
          marginTop: 24, padding: '10px 18px',
          background: '#1a2a1a', border: '1px solid #2a4a2a',
          borderRadius: 8, fontSize: 12, color: '#6a9955',
          fontFamily: "'Segoe UI', system-ui, sans-serif",
          textAlign: 'center', maxWidth: 420,
        }}>
          💡 Tip: Once connected, type <code style={{ color: '#4ec9b0', background: '#0d1a0d', padding: '1px 6px', borderRadius: 3 }}>/save docs</code> in the terminal to save your LLM session notes to the prune library
        </div>
      </div>
    )
  }

  const handleAddTab = () => {
    const newId = _tabIdCounter++
    setTabs(prev => [...prev, { id: newId, label: 'shell', relayPort: null, sessionNum: null }])
    setActiveTab(newId)
  }

  const handleCloseTab = (e, id) => {
    e.stopPropagation()
    setTabs(prev => {
      const next = prev.filter(t => t.id !== id)
      if (next.length === 0) {
        // Always keep at least one tab — replace with fresh
        const freshId = _tabIdCounter++
        setActiveTab(freshId)
        return [{ id: freshId, label: 'shell', relayPort: null, sessionNum: null }]
      }
      if (id === activeTab) {
        setActiveTab(next[next.length - 1].id)
      }
      return next
    })
  }

  return (
    <div style={{
      display: 'flex', flexDirection: 'column', height: '100%',
      background: '#1e1e1e', overflow: 'hidden',
    }}>
      {/* ── Tab bar ──────────────────────────────────────────────────── */}
      <div style={{
        display: 'flex', alignItems: 'stretch',
        background: '#2d2d2d', borderBottom: '1px solid #3c3c3c',
        height: 35, flexShrink: 0,
        fontFamily: "'Segoe UI', system-ui, sans-serif", fontSize: 12,
        overflowX: 'auto', overflowY: 'hidden',
      }}>
        {tabs.map(tab => {
          const isActive = tab.id === activeTab
          return (
            <div
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              style={{
                display: 'flex', alignItems: 'center', gap: 5,
                padding: '0 10px', cursor: 'pointer', whiteSpace: 'nowrap',
                borderRight: '1px solid #3c3c3c',
                borderBottom: isActive ? '1px solid #007acc' : '1px solid transparent',
                background: isActive ? '#1e1e1e' : 'transparent',
                color: isActive ? '#d4d4d4' : '#858585',
                transition: 'background 0.15s',
              }}
            >
              <span style={{ fontSize: 10 }}>⚡</span>
              <span>{tab.label}</span>
              {tab.relayPort && (
                <span style={{ color: '#555', fontSize: 10 }}>:{tab.relayPort}</span>
              )}
              <span
                onClick={e => handleCloseTab(e, tab.id)}
                style={{
                  marginLeft: 4, color: '#555', fontSize: 13, lineHeight: 1,
                  cursor: 'pointer', padding: '0 2px',
                  borderRadius: 3,
                }}
                onMouseEnter={e => e.currentTarget.style.color = '#d4d4d4'}
                onMouseLeave={e => e.currentTarget.style.color = '#555'}
                title="Close terminal"
              >×</span>
            </div>
          )
        })}

        {/* + New terminal button */}
        <div
          onClick={handleAddTab}
          title="New terminal"
          style={{
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            width: 30, cursor: 'pointer', color: '#858585', fontSize: 18,
            flexShrink: 0,
          }}
          onMouseEnter={e => e.currentTarget.style.color = '#d4d4d4'}
          onMouseLeave={e => e.currentTarget.style.color = '#858585'}
        >+</div>
      </div>

      {/* ── Panes — render all, hide inactive (preserves xterm state) ── */}
      <div style={{ flex: 1, overflow: 'hidden', position: 'relative' }}>
        {tabs.map(tab => (
          <div
            key={tab.id}
            style={{
              position: 'absolute', inset: 0,
              display: tab.id === activeTab ? 'flex' : 'none',
              flexDirection: 'column',
            }}
          >
            <TerminalPane
              tabId={tab.id}
              active={tab.id === activeTab}
              onTitleChange={handleTitleChange}
            />
          </div>
        ))}
      </div>
    </div>
  )
}
