/**
 * FolderSelector — Scout-driven folder/file selection for the Raw vs Pruned tab
 *
 * Flow:
 *   1. Scout picks relevant folders from knowledge graph
 *   2. User reviews, adds/removes folders and individual files
 *   3. User clicks OK → generates "query + file paths" prompt → copied to clipboard / shown in Assembled Prompt tab
 */

import React, { useState } from 'react'

export default function FolderSelector({
  scoutData,
  selectedFolderFiles,   // { folderPath: Set<filePath> }
  onSelectionChange,
  onOK,
  theme,
}) {
  const [search, setSearch] = useState('')
  const isDark = theme === 'dark'

  if (!scoutData) {
    return (
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        height: '100%', flexDirection: 'column', gap: 14,
        color: isDark ? '#8b949e' : '#656d76',
      }}>
        <div style={{ fontSize: 44 }}>🔍</div>
        <div style={{ fontSize: 16, fontWeight: 600 }}>Enter a query and click <strong>Scout Folders</strong></div>
        <div style={{ fontSize: 13, opacity: 0.7 }}>
          Scout (Llama via Groq) will analyse the knowledge graph and pick relevant folders + files
        </div>
      </div>
    )
  }

  const allFolders = scoutData.all_folders || {}
  const query = scoutData.query || ''

  const totalFiles = Object.values(selectedFolderFiles)
    .reduce((sum, files) => sum + files.size, 0)
  const totalFolders = Object.keys(selectedFolderFiles).length

  const unselectedFolders = Object.keys(allFolders)
    .filter(f => !selectedFolderFiles[f])
    .filter(f => !search || f.toLowerCase().includes(search.toLowerCase()))
    .sort()

  const selectedFolderKeys = Object.keys(selectedFolderFiles).sort()

  const addFolder = (folder) => {
    const files = allFolders[folder] || []
    onSelectionChange({ ...selectedFolderFiles, [folder]: new Set(files) })
  }

  const removeFolder = (folder) => {
    const next = { ...selectedFolderFiles }
    delete next[folder]
    onSelectionChange(next)
  }

  const toggleFile = (folder, file) => {
    const current = new Set(selectedFolderFiles[folder] || [])
    if (current.has(file)) {
      current.delete(file)
      if (current.size === 0) {
        removeFolder(folder)
      } else {
        onSelectionChange({ ...selectedFolderFiles, [folder]: current })
      }
    } else {
      current.add(file)
      onSelectionChange({ ...selectedFolderFiles, [folder]: current })
    }
  }

  const borderColor = isDark ? '#30363d' : '#d0d7de'
  const bg = isDark ? '#0d1117' : '#ffffff'
  const bgSub = isDark ? '#161b22' : '#f6f8fa'
  const textMain = isDark ? '#e6edf3' : '#1f2328'
  const textMuted = isDark ? '#8b949e' : '#656d76'

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', background: bg }}>

      {/* ── Header ── */}
      <div style={{
        padding: '12px 20px',
        borderBottom: `1px solid ${borderColor}`,
        background: bgSub,
        display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap',
      }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 11, color: textMuted, textTransform: 'uppercase', letterSpacing: '0.5px' }}>Scout Query</div>
          <div style={{ fontSize: 15, fontWeight: 700, color: textMain, marginTop: 2 }}>"{query}"</div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexShrink: 0 }}>
          <span style={{ fontSize: 13, color: textMuted }}>
            {totalFolders} folder{totalFolders !== 1 ? 's' : ''} · {totalFiles} file{totalFiles !== 1 ? 's' : ''}
          </span>
          <button
            onClick={onOK}
            disabled={totalFiles === 0}
            style={{
              padding: '8px 22px',
              background: totalFiles > 0 ? '#238636' : '#3d444d',
              color: '#fff',
              border: 'none',
              borderRadius: 6,
              cursor: totalFiles > 0 ? 'pointer' : 'not-allowed',
              fontWeight: 700,
              fontSize: 14,
              transition: 'background 0.2s',
            }}
          >
            ✓ OK — Build Prompt
          </button>
        </div>
      </div>

      {/* ── Body: two-panel ── */}
      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>

        {/* Left panel — scout-selected folders */}
        <div style={{ flex: 1, overflow: 'auto', padding: 16, borderRight: `1px solid ${borderColor}` }}>
          <div style={{ fontSize: 12, fontWeight: 700, color: isDark ? '#7ee787' : '#1a7f37', marginBottom: 12, textTransform: 'uppercase', letterSpacing: '0.5px' }}>
            ✓ Selected by Scout
          </div>

          {selectedFolderKeys.length === 0 && (
            <div style={{ fontSize: 13, color: textMuted, padding: '20px 0' }}>
              No folders selected — add from the right panel.
            </div>
          )}

          {selectedFolderKeys.map(folder => {
            const scoutEntry = scoutData.selected_folders?.[folder]
            const reasoning = scoutEntry?.reasoning || ''
            const isAnnotation = reasoning.startsWith('Included because')
            const allFilesForFolder = allFolders[folder] || []
            return (
              <SelectedFolderCard
                key={folder}
                folder={folder}
                selectedFiles={selectedFolderFiles[folder]}
                allFiles={allFilesForFolder}
                reasoning={reasoning}
                isAnnotation={isAnnotation}
                onRemove={() => removeFolder(folder)}
                onToggleFile={(file) => toggleFile(folder, file)}
                isDark={isDark}
                borderColor={borderColor}
                textMain={textMain}
                textMuted={textMuted}
                bgSub={bgSub}
              />
            )
          })}

          {/* Legend */}
          {selectedFolderKeys.length > 0 && (
            <div style={{ display: 'flex', gap: 16, marginTop: 8, padding: '8px 4px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: textMuted }}>
                <span style={{ width: 10, height: 10, borderRadius: 2, background: isDark ? '#238636' : '#1a7f37', display: 'inline-block' }} />
                Scout selected
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: textMuted }}>
                <span style={{ width: 10, height: 10, borderRadius: 2, background: isDark ? '#e3b341' : '#bf8700', display: 'inline-block' }} />
                Annotation matched
              </div>
            </div>
          )}
        </div>

        {/* Right panel — add more folders */}
        <div style={{ width: 280, overflow: 'auto', padding: 16, background: bgSub }}>
          <div style={{ fontSize: 12, fontWeight: 700, color: textMuted, marginBottom: 10, textTransform: 'uppercase', letterSpacing: '0.5px' }}>
            + Add Folders
          </div>
          <input
            placeholder="Search folders..."
            value={search}
            onChange={e => setSearch(e.target.value)}
            style={{
              width: '100%', padding: '7px 10px', fontSize: 13,
              borderRadius: 6, border: `1px solid ${borderColor}`,
              background: bg, color: textMain,
              marginBottom: 10, boxSizing: 'border-box',
              outline: 'none',
            }}
          />
          {unselectedFolders.map(folder => (
            <div
              key={folder}
              onClick={() => addFolder(folder)}
              title={`Add ${folder} (${(allFolders[folder] || []).length} files)`}
              style={{
                padding: '8px 12px', borderRadius: 6, cursor: 'pointer',
                marginBottom: 5, border: `1px solid ${borderColor}`,
                background: bg, transition: 'border-color 0.15s',
              }}
              onMouseEnter={e => e.currentTarget.style.borderColor = isDark ? '#58a6ff' : '#0969da'}
              onMouseLeave={e => e.currentTarget.style.borderColor = borderColor}
            >
              <div style={{ fontSize: 13, fontFamily: 'monospace', color: textMain, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                📁 {folder || '(root)'}
              </div>
              <div style={{ fontSize: 11, color: textMuted, marginTop: 2 }}>
                {(allFolders[folder] || []).length} files · click to add
              </div>
            </div>
          ))}
          {unselectedFolders.length === 0 && (
            <div style={{ fontSize: 12, color: textMuted }}>All folders selected</div>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Individual selected folder card ──────────────────────────────────────────

function SelectedFolderCard({
  folder, selectedFiles, allFiles, reasoning, isAnnotation,
  onRemove, onToggleFile,
  isDark, borderColor, textMain, textMuted, bgSub,
}) {
  const [expanded, setExpanded] = useState(true)
  const [showReasoning, setShowReasoning] = useState(false)

  // Scout = green, Annotation = amber
  const cardBorder = isAnnotation
    ? (isDark ? '#e3b341' : '#bf8700')
    : (isDark ? '#238636' : '#1a7f37')
  const cardBg = isAnnotation
    ? (isDark ? 'rgba(227,179,65,0.10)' : 'rgba(191,135,0,0.06)')
    : (isDark ? 'rgba(35,134,54,0.12)' : 'rgba(26,127,55,0.06)')
  const folderColor = isAnnotation
    ? (isDark ? '#e3b341' : '#bf8700')
    : (isDark ? '#7ee787' : '#1a7f37')
  const badgeBg = isAnnotation
    ? (isDark ? '#e3b341' : '#bf8700')
    : (isDark ? '#1f6feb' : '#0969da')
  const icon = isAnnotation ? '📌' : '📁'

  return (
    <div style={{
      marginBottom: 10,
      border: `1.5px solid ${cardBorder}`,
      borderRadius: 8,
      overflow: 'visible',
      position: 'relative',
    }}>
      {/* Folder header row */}
      <div
        style={{
          display: 'flex', alignItems: 'center', padding: '8px 12px',
          background: cardBg,
          cursor: 'pointer', borderRadius: expanded ? '6px 6px 0 0' : 6,
        }}
        onClick={() => setExpanded(e => !e)}
      >
        <span style={{ marginRight: 8, fontSize: 10, color: textMuted }}>{expanded ? '▼' : '▶'}</span>
        <span style={{ fontFamily: 'monospace', fontSize: 13, fontWeight: 700, color: folderColor, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {icon} {folder || '(root)'}
        </span>
        <span style={{ fontSize: 11, color: textMuted, marginRight: 8, flexShrink: 0 }}>
          {selectedFiles.size}/{allFiles.length}
        </span>

        {/* Reasoning hover badge */}
        {reasoning && (
          <span
            onMouseEnter={() => setShowReasoning(true)}
            onMouseLeave={() => setShowReasoning(false)}
            onClick={e => e.stopPropagation()}
            style={{
              marginRight: 8, width: 18, height: 18, borderRadius: '50%',
              background: badgeBg,
              color: isAnnotation ? '#1f2328' : '#fff',
              fontSize: 11, fontWeight: 700,
              display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
              cursor: 'help', flexShrink: 0,
            }}
            title={isAnnotation ? 'Matched by annotation' : 'Why was this scouted?'}
          >
            {isAnnotation ? '📌' : '?'}
          </span>
        )}

        {/* Remove button */}
        <button
          onClick={e => { e.stopPropagation(); onRemove() }}
          style={{
            background: 'none', border: 'none', cursor: 'pointer',
            color: isDark ? '#f85149' : '#cf222e', fontSize: 16,
            padding: '0 2px', lineHeight: 1, flexShrink: 0,
          }}
          title="Remove folder"
        >✕</button>
      </div>

      {/* Reasoning tooltip */}
      {showReasoning && reasoning && (
        <div style={{
          position: 'absolute',
          top: '100%',
          left: 0,
          zIndex: 9999,
          background: isDark ? '#1c2128' : '#ffffff',
          border: `1.5px solid ${cardBorder}`,
          borderRadius: 8,
          padding: '12px 16px',
          maxWidth: 420,
          minWidth: 280,
          fontSize: 13,
          color: isDark ? '#e6edf3' : '#1f2328',
          boxShadow: isDark ? '0 8px 32px rgba(0,0,0,0.6)' : '0 8px 24px rgba(0,0,0,0.15)',
          lineHeight: 1.6,
          pointerEvents: 'none',
        }}>
          <div style={{ fontWeight: 700, color: isAnnotation ? (isDark ? '#e3b341' : '#bf8700') : (isDark ? '#58a6ff' : '#0969da'), marginBottom: 6, fontSize: 12, display: 'flex', alignItems: 'center', gap: 6 }}>
            {isAnnotation ? '📌 Annotation Match' : '💡 Scout Reasoning'}
          </div>
          {reasoning}
        </div>
      )}

      {/* File checkboxes */}
      {expanded && (
        <div style={{ padding: '6px 12px 10px', borderTop: `1px solid ${cardBorder}` }}>
          {allFiles.length === 0 && (
            <div style={{ fontSize: 12, color: textMuted }}>No files indexed in this folder</div>
          )}
          {allFiles.map(file => {
            const filename = file.split('/').pop()
            const isChecked = selectedFiles.has(file)
            return (
              <label
                key={file}
                style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '3px 0', cursor: 'pointer' }}
              >
                <input
                  type="checkbox"
                  checked={isChecked}
                  onChange={() => onToggleFile(file)}
                  style={{ cursor: 'pointer', accentColor: '#238636', width: 14, height: 14 }}
                />
                <span style={{
                  fontFamily: 'monospace', fontSize: 13, fontWeight: 600,
                  color: isChecked ? textMain : (isDark ? '#484f58' : '#bbb'),
                }}>
                  {filename}
                </span>
                <span style={{ fontSize: 11, color: textMuted, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {file}
                </span>
              </label>
            )
          })}
        </div>
      )}
    </div>
  )
}
