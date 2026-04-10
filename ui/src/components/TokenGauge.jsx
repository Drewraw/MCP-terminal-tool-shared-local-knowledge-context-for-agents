import React, { useMemo } from 'react'

export default function TokenGauge({ savings = 0, compression = 1 }) {
  const pct = Math.min(Math.max(savings, 0), 100)
  
  // Determine color based on savings percentage
  const getColor = () => {
    if (pct >= 66) return '#3fb950'  // Neon green for 66%+
    if (pct >= 50) return '#58a6ff'  // Blue for 50-66%
    if (pct >= 25) return '#d29922'  // Yellow for 25-50%
    return '#f85149'                   // Red for <25%
  }
  
  const color = getColor()
  
  // SVG circle gauge calculation
  const radius = 45
  const circumference = 2 * Math.PI * radius
  const strokeDashoffset = circumference - (pct / 100) * circumference
  
  return (
    <div className="token-gauge-container">
      <div className="gauge-wrapper">
        <svg 
          viewBox="0 0 100 100"
          className="gauge-svg"
          style={{ width: '120px', height: '120px' }}
        >
          {/* Background circle */}
          <circle 
            cx="50" 
            cy="50" 
            r={radius} 
            stroke="#21262d" 
            strokeWidth="6" 
            fill="none"
          />
          
          {/* Progress circle with animated stroke */}
          <circle 
            cx="50" 
            cy="50" 
            r={radius} 
            stroke={color}
            strokeWidth="6" 
            fill="none"
            strokeDasharray={circumference}
            strokeDashoffset={strokeDashoffset}
            strokeLinecap="round"
            className="gauge-progress"
            style={{
              transition: 'stroke-dashoffset 0.6s ease-out, stroke 0.3s ease',
            }}
          />
        </svg>
        
        {/* Center text */}
        <div className="gauge-center">
          <div className="gauge-value" style={{ color }}>
            {pct.toFixed(0)}%
          </div>
          <div className="gauge-label">Saved</div>
        </div>
      </div>
      
      {/* Compression ratio below */}
      <div className="compression-stat">
        <div className="compression-value">
          {compression.toFixed(2)}x
        </div>
        <div className="compression-label">
          Compression
        </div>
      </div>
      
      {/* Action description */}
      <div className="savings-description">
        <span className="description-label">
          {pct >= 66 ? '🎯 Excellent' : pct >= 50 ? '✅ Good' : '⚠️ Minimal'} {' '}
          pruning
        </span>
      </div>
    </div>
  )
}
