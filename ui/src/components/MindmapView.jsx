import React, { useState, useEffect } from 'react'

export default function MindmapView({ mindmap, summary }) {
  const [expandedNodes, setExpandedNodes] = useState(new Set())
  const [annotations, setAnnotations] = useState({})
  const [editingPath, setEditingPath] = useState(null)
  const [editValue, setEditValue] = useState('')

  // Load annotations on mount
  useEffect(() => {
    fetch('/annotations')
      .then(r => r.json())
      .then(data => setAnnotations(data || {}))
      .catch(() => {})
  }, [])

  const toggleNode = (nodeId) => {
    const newExpanded = new Set(expandedNodes)
    if (newExpanded.has(nodeId)) {
      newExpanded.delete(nodeId)
    } else {
      newExpanded.add(nodeId)
    }
    setExpandedNodes(newExpanded)
  }

  const startEdit = (file_path, current_value) => {
    setEditingPath(file_path)
    setEditValue(current_value || '')
  }

  const saveAnnotation = async (file_path) => {
    try {
      const resp = await fetch('/annotations', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ file_path, annotation: editValue }),
      })
      const data = await resp.json()
      if (data.status === 'ok') {
        setAnnotations(prev => ({
          ...prev,
          [file_path]: editValue || undefined,
        }))
      }
    } catch (err) {
      console.error('Failed to save annotation:', err)
    }
    setEditingPath(null)
    setEditValue('')
  }

  const getNodeIcon = (type) => {
    const icons = {
      project: '📦',
      module: '📁',
      class: '🏛️',
      function: '⚙️',
      method: '🔧',
      interface: '📋',
      enum: '📊',
    }
    return icons[type] || '•'
  }

  const renderNode = (node, depth = 0, parentId = '') => {
    const nodeId = `${parentId}-${node.name}`
    const isExpanded = expandedNodes.has(nodeId)
    const hasChildren = node.children && node.children.length > 0
    const hasImports = node.imports && node.imports.length > 0
    const annotation = annotations[node.file_path]
    const isEditing = editingPath === node.file_path

    return (
      <div key={nodeId} style={{ marginLeft: `${depth * 16}px`, fontSize: 13 }}>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            padding: '4px 8px',
            cursor: hasChildren ? 'pointer' : 'default',
            borderRadius: 4,
            color: node.type === 'project' ? '#79c0ff' : '#d0d4d9',
          }}
        >
          {hasChildren && (
            <span style={{ fontSize: 12, color: '#8b949e' }} onClick={() => hasChildren && toggleNode(nodeId)}>
              {isExpanded ? '▼' : '▶'}
            </span>
          )}
          <span>{getNodeIcon(node.type)}</span>
          <span
            style={{ fontWeight: node.type === 'project' ? 600 : 400, cursor: hasChildren ? 'pointer' : 'default', flex: 1 }}
            onClick={() => hasChildren && toggleNode(nodeId)}
          >
            {node.name}
          </span>
          
          {/* Show annotation status or edit button */}
          {node.type === 'module' && node.file_path && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              {annotation && (
                <span
                  title={annotation}
                  style={{
                    fontSize: 10,
                    padding: '2px 6px',
                    backgroundColor: '#1f6feb',
                    color: '#c9d1d9',
                    borderRadius: 3,
                    textOverflow: 'ellipsis',
                    overflow: 'hidden',
                    maxWidth: '150px',
                    whiteSpace: 'nowrap',
                  }}
                >
                  💬 {annotation}
                </span>
              )}
              <button
                onClick={() => startEdit(node.file_path, annotation)}
                style={{
                  padding: '2px 8px',
                  fontSize: 10,
                  backgroundColor: 'transparent',
                  border: '1px solid #30363d',
                  color: '#8b949e',
                  borderRadius: 3,
                  cursor: 'pointer',
                }}
                title="Add note about this module"
              >
                ✏️
              </button>
            </div>
          )}
        </div>

        {/* Inline edit mode */}
        {isEditing && (
          <div
            style={{
              marginLeft: 16,
              marginTop: 6,
              padding: 8,
              backgroundColor: '#161b22',
              border: '1px solid #30363d',
              borderRadius: 4,
              display: 'flex',
              flexDirection: 'column',
              gap: 6,
            }}
          >
            <input
              type="text"
              placeholder="Add a note about this module..."
              value={editValue}
              onChange={(e) => setEditValue(e.target.value)}
              style={{
                padding: 6,
                backgroundColor: '#0d1117',
                border: '1px solid #30363d',
                color: '#c9d1d9',
                borderRadius: 3,
                fontSize: 11,
                fontFamily: 'monospace',
              }}
              autoFocus
            />
            <div style={{ display: 'flex', gap: 6 }}>
              <button
                onClick={() => saveAnnotation(node.file_path)}
                style={{
                  padding: '4px 12px',
                  backgroundColor: '#238636',
                  color: '#fff',
                  border: 'none',
                  borderRadius: 3,
                  fontSize: 10,
                  cursor: 'pointer',
                }}
              >
                Save
              </button>
              <button
                onClick={() => setEditingPath(null)}
                style={{
                  padding: '4px 12px',
                  backgroundColor: 'transparent',
                  color: '#8b949e',
                  border: '1px solid #30363d',
                  borderRadius: 3,
                  fontSize: 10,
                  cursor: 'pointer',
                }}
              >
                Cancel
              </button>
            </div>
          </div>
        )}

        {/* Show imports for modules */}
        {node.type === 'module' && hasImports && isExpanded && (
          <div style={{ marginLeft: 16, fontSize: 11, color: '#6e7681', marginTop: 2 }}>
            <strong>imports:</strong> {node.imports.join(', ')}
          </div>
        )}

        {/* Render children */}
        {isExpanded && hasChildren && (
          <div>
            {node.children.map((child) => renderNode(child, depth + 1, nodeId))}
          </div>
        )}
      </div>
    )
  }

  if (!mindmap) {
    return (
      <div style={{
        padding: 16,
        color: '#8b949e',
        textAlign: 'center',
        fontSize: 14,
      }}>
        Click "Scan Project" to generate mindmap
      </div>
    )
  }

  return (
    <div style={{
      padding: 12,
      backgroundColor: '#0d1117',
      borderRadius: 8,
      fontSize: 12,
      fontFamily: 'monospace',
      color: '#c9d1d9',
      maxHeight: '500px',
      overflowY: 'auto',
    }}>
      {/* Summary Stats */}
      {summary && (
        <div style={{
          marginBottom: 16,
          padding: 12,
          backgroundColor: '#161b22',
          borderRadius: 4,
          borderLeft: '3px solid #58a6ff',
        }}>
          <div style={{ fontWeight: 600, marginBottom: 8, color: '#79c0ff' }}>
            📊 Architecture Summary
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
            <div>📁 Modules: <strong>{summary.total_modules}</strong></div>
            <div>🏛️ Classes: <strong>{summary.total_classes}</strong></div>
            <div>⚙️ Functions: <strong>{summary.total_functions}</strong></div>
          </div>
        </div>
      )}

      {/* Project Tree */}
      <div style={{
        padding: 12,
        backgroundColor: '#161b22',
        borderRadius: 4,
        border: '1px solid #30363d',
      }}>
        <div style={{ fontWeight: 600, marginBottom: 12, color: '#79c0ff' }}>
          🗂️ Project Structure
        </div>
        {renderNode(mindmap)}
      </div>

      {/* Legend */}
      <div style={{
        marginTop: 12,
        padding: 12,
        backgroundColor: '#161b22',
        borderRadius: 4,
        fontSize: 11,
        color: '#6e7681',
      }}>
        <div style={{ fontWeight: 600, marginBottom: 8, color: '#8b949e' }}>Legend:</div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 16 }}>
          <div>📦 Project</div>
          <div>📁 Module</div>
          <div>🏛️ Class</div>
          <div>📋 Interface</div>
          <div>⚙️ Function</div>
          <div>🔧 Method</div>
        </div>
      </div>
    </div>
  )
}
