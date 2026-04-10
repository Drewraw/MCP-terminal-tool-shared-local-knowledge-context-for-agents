import React, { useState } from 'react'

export default function DiffView({ file }) {
  const [showRaw, setShowRaw] = useState(true)
  const [showPruned, setShowPruned] = useState(true)

  if (!file) {
    return (
      <div className="diff-container empty">
        <p>Select a file to view the diff</p>
      </div>
    )
  }

  const rawSavings = file.raw_tokens > 0
    ? ((file.raw_tokens - file.pruned_tokens) / file.raw_tokens * 100).toFixed(1)
    : 0
  
  const lineSavings = file.raw_lines > 0
    ? ((file.raw_lines - file.pruned_lines) / file.raw_lines * 100).toFixed(1)
    : 0

  return (
    <div className="diff-container">
      {/* Controls */}
      <div className="diff-controls">
        <label className="diff-toggle">
          <input
            type="checkbox"
            checked={showRaw}
            onChange={e => setShowRaw(e.target.checked)}
          />
          Raw Code
        </label>
        <label className="diff-toggle">
          <input
            type="checkbox"
            checked={showPruned}
            onChange={e => setShowPruned(e.target.checked)}
          />
          Pruned Code
        </label>
      </div>

      <div className="diff-panes" style={{
        gridTemplateColumns: `${showRaw && showPruned ? '1fr 1fr' : '1fr'}`,
      }}>
        {/* Raw (left) */}
        {showRaw && (
          <div className="diff-pane raw">
            <div className="diff-pane-header">
              <span className="pane-title">📄 Raw Code</span>
              <span className="file-path">{file.file_path}</span>
            </div>
            <div className="pane-stats">
              <span className="stat-badge tokens">{file.raw_tokens.toLocaleString()} tokens</span>
              <span className="stat-badge lines">{file.raw_lines} lines</span>
            </div>
            <pre className="diff-code raw-content">
              {renderCodeWithLineNumbers(file.raw_content)}
            </pre>
          </div>
        )}

        {/* Pruned (right) */}
        {showPruned && (
          <div className="diff-pane pruned">
            <div className="diff-pane-header">
              <span className="pane-title">✂️ Pruned Code</span>
              <span className="savings-badge" style={{ color: '#3fb950' }}>
                -{rawSavings}% tokens, -{lineSavings}% lines
              </span>
            </div>
            <div className="pane-stats">
              <span className="stat-badge tokens" style={{color: '#3fb950'}}>
                {file.pruned_tokens.toLocaleString()} tokens
              </span>
              <span className="stat-badge lines" style={{color: '#3fb950'}}>
                {file.pruned_lines} lines
              </span>
            </div>
            <pre className="diff-code pruned-content">
              {renderPrunedContent(file.pruned_content)}
            </pre>
          </div>
        )}
      </div>

      {/* Footer stats */}
      {file.kept_symbols && file.kept_symbols.length > 0 && (
        <div className="diff-footer">
          <div className="kept-symbols">
            <strong>Kept symbols:</strong>
            {file.kept_symbols.slice(0, 8).map((sym, i) => (
              <span key={i} className="symbol-tag">{sym}</span>
            ))}
            {file.kept_symbols.length > 8 && (
              <span className="symbol-tag">+{file.kept_symbols.length - 8} more</span>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

function renderCodeWithLineNumbers(content) {
  if (!content) return null
  const lines = content.split('\n')
  return (
    <div className="code-lines">
      {lines.map((line, i) => (
        <div key={i} className="code-line">
          <span className="line-number">{String(i + 1).padStart(4, ' ')}</span>
          <span className="line-content">{line}</span>
        </div>
      ))}
    </div>
  )
}

function renderPrunedContent(content) {
  if (!content) return null
  const lines = content.split('\n')
  return (
    <div className="code-lines">
      {lines.map((line, i) => {
        const isPruneMarker = /# \.\.\. \[\d+ lines pruned\]/.test(line)
        return (
          <div key={i} className={`code-line ${isPruneMarker ? 'pruned-marker' : ''}`}>
            <span className="line-number">{String(i + 1).padStart(4, ' ')}</span>
            <span className="line-content">
              {isPruneMarker ? (
                <span className="marker-badge">{line}</span>
              ) : (
                line
              )}
            </span>
          </div>
        )
      })}
    </div>
  )
}
