import React from 'react'

export default function PromptView({ prompt, goalHint }) {
  if (!prompt) return null

  // Anthropic format: { system: [...], messages: [...] }
  // OpenAI format: { messages: [...] }
  const systemBlocks = prompt.system || []
  const messages = prompt.messages || []

  return (
    <div className="prompt-view">
      {goalHint && (
        <div style={{
          padding: '8px 14px',
          background: 'rgba(188, 140, 255, 0.1)',
          border: '1px solid rgba(188, 140, 255, 0.3)',
          borderRadius: 'var(--radius)',
          marginBottom: 16,
          fontSize: 13,
          color: '#bc8cff',
        }}>
          Goal Hint: {goalHint}
        </div>
      )}

      {/* System blocks */}
      {systemBlocks.map((block, i) => (
        <div key={`sys-${i}`} className="prompt-block">
          <div className="prompt-block-header">
            <span>System Block {i + 1}</span>
            {block.cache_control && (
              <span style={{
                fontSize: 10,
                padding: '2px 8px',
                borderRadius: 10,
                background: 'rgba(63, 185, 80, 0.15)',
                color: '#3fb950',
              }}>
                cache_control: {block.cache_control.type}
              </span>
            )}
          </div>
          <div className="prompt-block-body">
            {block.text}
          </div>
        </div>
      ))}

      {/* For OpenAI-format where system is in messages */}
      {systemBlocks.length === 0 && messages.filter(m => m.role === 'system').map((msg, i) => (
        <div key={`sys-msg-${i}`} className="prompt-block">
          <div className="prompt-block-header">
            <span>System Message</span>
          </div>
          <div className="prompt-block-body">
            {msg.content}
          </div>
        </div>
      ))}

      {/* User messages */}
      {messages.filter(m => m.role === 'user').map((msg, i) => (
        <div key={`user-${i}`} className="prompt-block">
          <div className="prompt-block-header">
            <span>User Query</span>
            <span style={{
              fontSize: 10,
              padding: '2px 8px',
              borderRadius: 10,
              background: 'rgba(88, 166, 255, 0.15)',
              color: '#58a6ff',
            }}>
              varies per turn
            </span>
          </div>
          <div className="prompt-block-body">
            {msg.content}
          </div>
        </div>
      ))}
    </div>
  )
}
