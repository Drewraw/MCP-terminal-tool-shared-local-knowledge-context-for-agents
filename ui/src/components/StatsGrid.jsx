import React from 'react'

export default function StatsGrid({ stats = {}, cacheInfo = {} }) {
  const {
    total_raw_tokens = 0,
    total_pruned_tokens = 0,
    compression_ratio = 1,
    token_savings_pct = 0,
    files_processed = 0,
    symbols_matched = 0,
  } = stats

  const {
    cache_hit_likely = false,
    system_tokens = 0,
    code_tokens = 0,
    query_tokens = 0,
    total_tokens = 0,
  } = cacheInfo

  return (
    <div className="stats-grid">
      {/* Main Metrics Row */}
      <div className="stats-row primary">
        <div className="stat-card token-reduction">
          <div className="stat-icon">📉</div>
          <div className="stat-value" style={{ color: '#3fb950' }}>
            {token_savings_pct.toFixed(1)}%
          </div>
          <div className="stat-label">Token Savings</div>
          <div className="stat-detail">
            {total_raw_tokens.toLocaleString()} → {total_pruned_tokens.toLocaleString()}
          </div>
        </div>

        <div className="stat-card compression">
          <div className="stat-icon">🗜️</div>
          <div className="stat-value">
            {compression_ratio.toFixed(2)}x
          </div>
          <div className="stat-label">Compression</div>
          <div className="stat-detail">
            {((compression_ratio - 1) * 100).toFixed(0)}% smaller
          </div>
        </div>

        <div className="stat-card files">
          <div className="stat-icon">📁</div>
          <div className="stat-value">
            {files_processed}
          </div>
          <div className="stat-label">Files</div>
          <div className="stat-detail">
            {symbols_matched} symbols kept
          </div>
        </div>
      </div>

      {/* Secondary Metrics Row - Cache Info */}
      {total_tokens > 0 && (
        <div className="stats-row secondary">
          <div className="stat-card cache-status">
            <div className="stat-icon">
              {cache_hit_likely ? '⚡' : '🔄'}
            </div>
            <div className="stat-value" style={{
              color: cache_hit_likely ? '#3fb950' : '#58a6ff'
            }}>
              {cache_hit_likely ? 'Cache Hit' : 'New Request'}
            </div>
            <div className="stat-label">
              {cache_hit_likely ? 'Likely' : 'Expected'}
            </div>
          </div>

          <div className="stat-card breakdown">
            <div className="stat-icon">📊</div>
            <div className="breakdown-items">
              <div className="breakdown-item">
                <span>System:</span>
                <span>{system_tokens.toLocaleString()}t</span>
              </div>
              <div className="breakdown-item">
                <span>Code:</span>
                <span>{code_tokens.toLocaleString()}t</span>
              </div>
              <div className="breakdown-item">
                <span>Query:</span>
                <span>{query_tokens.toLocaleString()}t</span>
              </div>
            </div>
          </div>

          <div className="stat-card total">
            <div className="stat-icon">📈</div>
            <div className="stat-value">
              {total_tokens.toLocaleString()}
            </div>
            <div className="stat-label">Total API Tokens</div>
            <div className="stat-detail">
              Original: {total_raw_tokens.toLocaleString()}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
