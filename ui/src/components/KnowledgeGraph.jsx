/**
 * Knowledge Graph — 3-Level Mind-Map Dependency Visualization
 * =============================================================
 * Structure (left → right):
 *   [Folder Header]  →  [Files Block]  →  [Dep Folder Header]  →  [Dep Files Block]  → ...
 *
 * Every folder is split into two linked nodes:
 *   • folderHeader  — compact box: folder path + file count badge
 *   • filesBlock    — files listed vertically in monospace
 *
 * Import edges connect: filesBlock_A → folderHeader_B
 * This gives the cascading mind-map look with dagre LR layout.
 */

import React, { useEffect, useCallback, useState } from 'react'
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  Handle,
  Position,
  MarkerType,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import dagre from '@dagrejs/dagre'

// ── Layout ────────────────────────────────────────────────────────

const FH_W = 210   // folderHeader width
const FB_W = 220   // filesBlock width
const FH_H = 64    // folderHeader fixed height
const FILE_ROW = 26 // px per file row (fontSize 14 * lineHeight 1.7 ≈ 24 + divider)
const FB_PAD = 32  // top+bottom padding in filesBlock

function filesBlockHeight(fileCount) {
  return FB_PAD + Math.min(fileCount, 12) * FILE_ROW + (fileCount > 12 ? FILE_ROW : 0)
}

function computeLayout(nodes, edges) {
  const g = new dagre.graphlib.Graph()
  g.setDefaultEdgeLabel(() => ({}))
  g.setGraph({ rankdir: 'LR', nodesep: 60, ranksep: 80, acyclicer: 'greedy' })

  nodes.forEach((n) => {
    const w = n.type === 'folderHeader' ? FH_W : FB_W
    const h = n.type === 'folderHeader' ? FH_H : filesBlockHeight(n.data.files?.length || 0)
    g.setNode(n.id, { width: w, height: h })
  })
  edges.forEach((e) => g.setEdge(e.source, e.target))
  dagre.layout(g)

  return nodes.map((n) => {
    const pos = g.node(n.id)
    const w = n.type === 'folderHeader' ? FH_W : FB_W
    const h = n.type === 'folderHeader' ? FH_H : filesBlockHeight(n.data.files?.length || 0)
    return { ...n, position: { x: pos.x - w / 2, y: pos.y - h / 2 } }
  })
}

// ── Colour palettes ───────────────────────────────────────────────

function palette(dark, isActive, isDimmed) {
  if (dark) return {
    fhBg:      isActive ? '#0f2a45' : isDimmed ? '#0a0f1a' : '#161e2e',
    fhBorder:  isActive ? '#00cfff' : isDimmed ? '#0f1e30' : '#1e3a5f',
    fhText:    isDimmed ? '#2a3f55' : '#ffffff',
    fbBg:      isActive ? '#0c2035' : isDimmed ? '#090d17' : '#111826',
    fbBorder:  isActive ? '#00cfff' : isDimmed ? '#0a1525' : '#18304e',
    fileText:  isDimmed ? '#1e3a55' : '#ffffff',
    badgeBg:   'rgba(0,180,255,0.15)',
    badgeCol:  '#7dd8f8',
    badgeBdr:  '#1e4a6e',
    divider:   '#1a2e45',
    muted:     '#4a6a80',
    importCol: '#00cfff',
    canvasBg:  '#090e1a',
    dotCol:    '#141f30',
    edgeCol:   '#1a6a9e',
    edgeActive:'#00cfff',
    edgeDim:   '#0a2035',
  }
  // light — mint/teal
  return {
    fhBg:      isActive ? '#9ef0d4' : isDimmed ? '#f0faf6' : '#c8f5e8',
    fhBorder:  isActive ? '#2cb88a' : isDimmed ? '#c8e8de' : '#6ed4b0',
    fhText:    isDimmed ? '#aac8c0' : '#000000',
    fbBg:      isActive ? '#e0faf2' : isDimmed ? '#f7fdfa' : '#eafaf4',
    fbBorder:  isActive ? '#2cb88a' : isDimmed ? '#d0ece4' : '#a8e8d0',
    fileText:  isDimmed ? '#b0c8c4' : '#111111',
    badgeBg:   'rgba(44,184,138,0.2)',
    badgeCol:  '#0a5c40',
    badgeBdr:  '#6ed4b0',
    divider:   '#b8e8d8',
    muted:     '#7ab8a4',
    importCol: '#2cb88a',
    canvasBg:  '#e8f8f3',
    dotCol:    '#b2e8d4',
    edgeCol:   '#2cb88a',
    edgeActive:'#0d9e72',
    edgeDim:   '#c0e8da',
  }
}

// ── FolderHeader node ─────────────────────────────────────────────

function FolderHeaderNode({ data }) {
  const dark = data.theme !== 'light'
  const p = palette(dark, data.isActive, data.isDimmed)
  const op = data.isDimmed ? 0.35 : 1

  return (
    <div style={{
      background: p.fhBg,
      border: `2px solid ${p.fhBorder}`,
      borderRadius: 8,
      padding: '8px 12px',
      width: FH_W,
      boxSizing: 'border-box',
      opacity: op,
      boxShadow: data.isActive
        ? `0 0 12px ${dark ? 'rgba(0,207,255,0.35)' : 'rgba(44,184,138,0.35)'}`
        : 'none',
      transition: 'all 0.25s ease',
      position: 'relative',
    }}>
      <Handle type="target" position={Position.Left}
        style={{ background: p.fhBorder, width: 7, height: 7, border: 'none' }} />
      <Handle type="source" position={Position.Right}
        style={{ background: p.fhBorder, width: 7, height: 7, border: 'none' }} />

      {/* Folder path */}
      <div style={{
        fontSize: 14, fontWeight: 700, color: p.fhText,
        lineHeight: 1.35, wordBreak: 'break-all', marginBottom: 4,
      }}>
        {data.label}
      </div>

      {/* File count badge */}
      <span style={{
        display: 'inline-block',
        background: p.badgeBg,
        border: `1px solid ${p.badgeBdr}`,
        borderRadius: 8, padding: '0 6px',
        fontSize: 9.5, fontWeight: 600, color: p.badgeCol,
      }}>
        {data.fileCount} files
      </span>

      {/* Active dot */}
      {data.isActive && (
        <div style={{
          position: 'absolute', top: -4, right: -4,
          width: 8, height: 8, borderRadius: '50%',
          background: dark ? '#00cfff' : '#2cb88a',
          border: `2px solid ${dark ? '#090e1a' : '#e8f8f3'}`,
          animation: 'pulse 2s infinite',
        }} />
      )}
    </div>
  )
}

// ── FilesBlock node ───────────────────────────────────────────────

function FilesBlockNode({ data }) {
  const dark = data.theme !== 'light'
  const p = palette(dark, data.isActive, data.isDimmed)
  const op = data.isDimmed ? 0.3 : 1
  const files = data.files || []
  const show  = files.slice(0, 12)
  const extra = files.length - show.length

  return (
    <div style={{
      background: p.fbBg,
      border: `2px solid ${p.fbBorder}`,
      borderRadius: 6,
      padding: '8px 10px',
      width: FB_W,
      boxSizing: 'border-box',
      opacity: op,
      transition: 'all 0.25s ease',
    }}>
      <Handle type="target" position={Position.Left}
        style={{ background: p.fbBorder, width: 6, height: 6, border: 'none' }} />
      <Handle type="source" position={Position.Right}
        style={{ background: p.fbBorder, width: 6, height: 6, border: 'none' }} />

      {show.map((f, i) => (
        <div key={i} style={{
          fontSize: 14, color: p.fileText,
          fontFamily: 'monospace', lineHeight: 1.7,
          whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
          borderBottom: i < show.length - 1 ? `1px solid ${p.divider}` : 'none',
        }}>
          {f}
        </div>
      ))}
      {extra > 0 && (
        <div style={{ fontSize: 9.5, color: p.muted, fontStyle: 'italic', marginTop: 2 }}>
          +{extra} more
        </div>
      )}
    </div>
  )
}

const nodeTypes = { folderHeader: FolderHeaderNode, filesBlock: FilesBlockNode }

// ── Main Component ────────────────────────────────────────────────

export default function KnowledgeGraph({ graphData, activeFolderIds, theme = 'dark' }) {
  const isDark = theme === 'dark'
  const [nodes, setNodes, onNodesChange] = useNodesState([])
  const [edges, setEdges, onEdgesChange] = useEdgesState([])
  const [searchTerm, setSearchTerm] = useState('')

  const buildGraph = useCallback(() => {
    if (!graphData?.nodes?.length) return

    const dark = theme !== 'light'
    const p = palette(dark, false, false)

    const activeSet = activeFolderIds ? new Set(activeFolderIds) : null

    // ── Build node + edge lists ───────────────────────────────────

    const rawNodes = []
    const rawEdges = []

    // Map: original folder id → {folderHeader id, filesBlock id}
    const folderIds = {}

    for (const n of graphData.nodes) {
      const fid = n.id
      const files = n.data.files || []
      const isActive = activeSet ? activeSet.has(fid) : false
      const isDimmed = activeSet ? !activeSet.has(fid) : false

      const fhId = `fh__${fid}`
      const fbId = `fb__${fid}`
      folderIds[fid] = { fhId, fbId }

      // Folder header node
      rawNodes.push({
        id: fhId,
        type: 'folderHeader',
        position: { x: 0, y: 0 },
        data: {
          label: fid,
          fileCount: n.data.fileCount || files.length,
          files,
          theme,
          isActive,
          isDimmed,
        },
      })

      // Files block node
      if (files.length > 0) {
        rawNodes.push({
          id: fbId,
          type: 'filesBlock',
          position: { x: 0, y: 0 },
          data: { files, theme, isActive, isDimmed },
        })

        // Connector edge: folderHeader → filesBlock (thin, same-color)
        rawEdges.push({
          id: `e_conn__${fid}`,
          source: fhId,
          target: fbId,
          type: 'smoothstep',
          style: {
            stroke: isActive ? p.edgeActive : (isDimmed ? p.edgeDim : p.edgeCol),
            strokeWidth: 2,
            opacity: isDimmed ? 0.3 : 1,
          },
        })
      }
    }

    // Import edges: filesBlock_A → folderHeader_B
    const edgeColor = dark ? p.edgeCol : p.edgeCol
    for (const e of graphData.edges) {
      const src = folderIds[e.source]
      const tgt = folderIds[e.target]
      if (!src || !tgt) continue
      const srcNode = src.fbId  // from files block
      const tgtNode = tgt.fhId  // to folder header
      const weight  = e.data?.weight || 1
      rawEdges.push({
        id: `e_imp__${e.source}__${e.target}`,
        source: srcNode,
        target: tgtNode,
        type: 'smoothstep',
        markerEnd: { type: MarkerType.ArrowClosed, width: 9, height: 9, color: edgeColor },
        style: {
          stroke: edgeColor,
          strokeWidth: 2,
          opacity: 1,
        },
      })
    }

    // ── Layout ───────────────────────────────────────────────────
    const laid = computeLayout(rawNodes, rawEdges)
    setNodes(laid)
    setEdges(rawEdges)
  }, [graphData, theme, activeFolderIds])

  useEffect(() => { buildGraph() }, [buildGraph])

  // ── Update active/dim on activeFolderIds change ───────────────
  useEffect(() => {
    if (!graphData?.nodes) return
    const activeSet = activeFolderIds ? new Set(activeFolderIds) : null
    const dark = theme !== 'light'

    setNodes((prev) => prev.map((n) => {
      const fid = n.id.replace(/^(fh__|fb__)/, '')
      const isActive = activeSet ? activeSet.has(fid) : false
      const isDimmed = activeSet ? !activeSet.has(fid) : false
      return { ...n, data: { ...n.data, theme, isActive, isDimmed } }
    }))

    setEdges((prev) => prev.map((e) => {
      if (e.id.startsWith('e_conn__')) return e
      const p = palette(dark, false, false)
      const srcFid = e.source.replace('fb__', '')
      const tgtFid = e.target.replace('fh__', '')
      const bothActive = activeSet && activeSet.has(srcFid) && activeSet.has(tgtFid)
      const oneActive  = activeSet && (activeSet.has(srcFid) || activeSet.has(tgtFid))
      const col = bothActive ? palette(dark, true, false).edgeActive
                : oneActive  ? p.edgeCol
                : activeSet  ? p.edgeDim
                : p.edgeCol
      return {
        ...e,
        animated: bothActive,
        style: {
          ...e.style,
          stroke: col,
          opacity: activeSet ? (bothActive ? 1 : oneActive ? 0.6 : 0.2) : 0.85,
          strokeWidth: bothActive ? 2.5 : e.style?.strokeWidth,
        },
        markerEnd: { ...e.markerEnd, color: col },
      }
    }))
  }, [activeFolderIds, graphData, theme])

  // ── Search filter ─────────────────────────────────────────────
  useEffect(() => {
    if (!graphData?.nodes) return
    const dark = theme !== 'light'

    if (!searchTerm.trim()) {
      // Revert to activeFolderIds state
      const activeSet = activeFolderIds ? new Set(activeFolderIds) : null
      setNodes(prev => prev.map(n => {
        const fid = n.id.replace(/^(fh__|fb__)/, '')
        const isActive = activeSet ? activeSet.has(fid) : false
        const isDimmed = activeSet ? !activeSet.has(fid) : false
        return { ...n, data: { ...n.data, isActive, isDimmed } }
      }))
      return
    }

    const term = searchTerm.toLowerCase()

    // Find folders that match by folder name or file name
    const matched = new Set()
    for (const n of graphData.nodes) {
      const fid = n.id
      if (fid.toLowerCase().includes(term)) { matched.add(fid); continue }
      const files = n.data.files || []
      if (files.some(f => f.toLowerCase().includes(term))) matched.add(fid)
    }

    // Expand to directly connected folders (one hop)
    const connected = new Set(matched)
    for (const e of graphData.edges) {
      if (matched.has(e.source)) connected.add(e.target)
      if (matched.has(e.target)) connected.add(e.source)
    }

    setNodes(prev => prev.map(n => {
      const fid = n.id.replace(/^(fh__|fb__)/, '')
      const isActive = matched.has(fid)
      const isDimmed = !connected.has(fid)
      return { ...n, data: { ...n.data, isActive, isDimmed } }
    }))
  }, [searchTerm, graphData, activeFolderIds, theme])

  const p0 = palette(isDark, false, false)
  const stats = graphData?.stats || {}
  const activeCount = activeFolderIds?.length || 0

  if (!graphData?.nodes?.length) {
    return (
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        height: '100%', color: isDark ? '#4a6a80' : '#4a9078',
        fontSize: 14, flexDirection: 'column', gap: 12,
        background: p0.canvasBg,
      }}>
        <div style={{ fontSize: 32 }}>🕸</div>
        <div>No graph data. Click <strong>Scan Project</strong> to build the dependency graph.</div>
      </div>
    )
  }

  return (
    <div style={{ width: '100%', height: '100%', position: 'relative' }}>
      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; transform: scale(1); }
          50%       { opacity: 0.5; transform: scale(1.4); }
        }
        .react-flow__controls-button {
          background-color: ${isDark ? '#161e2e' : '#c8f5e8'} !important;
          border: 1px solid ${isDark ? '#1e3a5f' : '#6ed4b0'} !important;
        }
        .react-flow__controls-button svg {
          stroke: ${isDark ? '#5bc8f5' : '#0d5c40'} !important;
          fill: none !important;
        }
      `}</style>

      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.12 }}
        minZoom={0.05}
        maxZoom={3}
        proOptions={{ hideAttribution: true }}
        style={{ background: p0.canvasBg }}
      >
        <Background color={p0.dotCol} gap={22} size={1} variant="dots" />
        <Controls
          showInteractive={false}
          style={{
            background: isDark ? '#161e2e' : '#c8f5e8',
            border: `1px solid ${isDark ? '#1e3a5f' : '#6ed4b0'}`,
            borderRadius: 6,
          }}
        />
        <MiniMap
          nodeColor={(n) => {
            if (n.data?.isActive) return isDark ? '#00cfff' : '#2cb88a'
            if (n.data?.isDimmed) return isDark ? '#0a1525' : '#c0e8da'
            return n.type === 'folderHeader'
              ? (isDark ? '#1e3a5f' : '#6ed4b0')
              : (isDark ? '#111826' : '#a8e8d0')
          }}
          maskColor={isDark ? 'rgba(9,14,26,0.75)' : 'rgba(232,248,243,0.75)'}
          style={{
            background: isDark ? '#090e1a' : '#d4f5eb',
            border: `1px solid ${isDark ? '#1e3a5f' : '#6ed4b0'}`,
            borderRadius: 6,
          }}
        />

        {/* Search bar */}
        <div style={{
          position: 'absolute', top: 12, right: 12, zIndex: 5,
          display: 'flex', alignItems: 'center', gap: 6,
        }}>
          <input
            type="text"
            placeholder="🔍  Search files or folders…"
            value={searchTerm}
            onChange={e => setSearchTerm(e.target.value)}
            style={{
              background: isDark ? 'rgba(22,30,46,0.95)' : 'rgba(200,245,232,0.95)',
              border: `1px solid ${isDark ? '#1e3a5f' : '#6ed4b0'}`,
              borderRadius: 8,
              padding: '7px 14px',
              fontSize: 13,
              color: isDark ? '#ffffff' : '#000000',
              outline: 'none',
              width: 240,
              boxShadow: isDark ? '0 2px 8px rgba(0,0,0,0.4)' : '0 2px 8px rgba(0,0,0,0.1)',
            }}
          />
          {searchTerm && (
            <button
              onClick={() => setSearchTerm('')}
              style={{
                position: 'absolute', right: 10,
                background: 'transparent', border: 'none',
                color: isDark ? '#4a6a80' : '#7ab8a4',
                cursor: 'pointer', fontSize: 16, lineHeight: 1,
              }}
            >×</button>
          )}
        </div>

        {/* Stats panel */}
        <div style={{
          position: 'absolute', top: 12, left: 12, zIndex: 5,
          background: isDark ? 'rgba(22,30,46,0.92)' : 'rgba(200,245,232,0.92)',
          border: `1px solid ${isDark ? '#1e3a5f' : '#6ed4b0'}`,
          borderRadius: 8, padding: '8px 14px',
          display: 'flex', gap: 18, fontSize: 11,
          color: isDark ? '#4a8aaa' : '#2a7a60',
        }}>
          <span><strong style={{ color: isDark ? '#cdd9e5' : '#0d5c40' }}>{stats.total_folders || 0}</strong> folders</span>
          <span><strong style={{ color: isDark ? '#cdd9e5' : '#0d5c40' }}>{stats.total_edges || 0}</strong> edges</span>
          {activeCount > 0 && (
            <span><strong style={{ color: isDark ? '#00cfff' : '#2cb88a' }}>{activeCount}</strong> active</span>
          )}
        </div>
      </ReactFlow>
    </div>
  )
}
