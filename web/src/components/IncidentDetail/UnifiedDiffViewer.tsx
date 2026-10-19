import React from 'react'
import { CopyButton } from '../Common/CopyButton'
import { FileCode } from 'lucide-react'

interface UnifiedDiffViewerProps {
  diff: string | null | undefined
  filePath?: string | null
}

interface DiffLine {
  type: 'add' | 'del' | 'hunk' | 'header' | 'normal'
  text: string
  oldNum?: number | string
  newNum?: number | string
}

export const UnifiedDiffViewer: React.FC<UnifiedDiffViewerProps> = ({ diff, filePath }) => {
  if (!diff || diff.trim() === '') {
    return (
      <div className="state-box">
        <p className="state-box-desc">No patch diff available for this incident.</p>
      </div>
    )
  }

  // Parse lines for line numbers and syntax classification
  const lines = diff.split('\n')
  let currentOldLine = 0
  let currentNewLine = 0

  const parsedLines: DiffLine[] = lines.map((line) => {
    if (line.startsWith('@@')) {
      // Hunk header e.g. @@ -10,6 +10,7 @@
      const match = line.match(/@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/)
      if (match) {
        currentOldLine = parseInt(match[1], 10)
        currentNewLine = parseInt(match[2], 10)
      }
      return { type: 'hunk', text: line }
    }
    if (
      line.startsWith('---') ||
      line.startsWith('+++') ||
      line.startsWith('diff --git') ||
      line.startsWith('index ')
    ) {
      return { type: 'header', text: line }
    }
    if (line.startsWith('+')) {
      const num = currentNewLine++
      return { type: 'add', text: line, newNum: num }
    }
    if (line.startsWith('-')) {
      const num = currentOldLine++
      return { type: 'del', text: line, oldNum: num }
    }
    // Context line
    const oldNum = currentOldLine++
    const newNum = currentNewLine++
    return { type: 'normal', text: line, oldNum, newNum }
  })

  return (
    <div className="diff-container" tabIndex={0} aria-label="Unified Diff Viewer">
      <div className="diff-header">
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <FileCode size={14} style={{ color: 'var(--text-link)' }} />
          <span className="mono" style={{ fontWeight: 600 }}>
            {filePath || 'Unified Diff Patch'}
          </span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
            {lines.length} lines
          </span>
          <CopyButton text={diff} label="Patch Diff" />
        </div>
      </div>
      <div className="diff-body">
        {parsedLines.map((lineObj, idx) => {
          let lineClass = 'diff-line diff-line-normal'
          if (lineObj.type === 'add') lineClass = 'diff-line diff-line-add'
          else if (lineObj.type === 'del') lineClass = 'diff-line diff-line-del'
          else if (lineObj.type === 'hunk') lineClass = 'diff-line diff-line-hunk'
          else if (lineObj.type === 'header') lineClass = 'diff-line'

          return (
            <div key={idx} className={lineClass}>
              <span className="diff-line-num">{lineObj.oldNum ?? ''}</span>
              <span className="diff-line-num">{lineObj.newNum ?? ''}</span>
              <span style={{ whiteSpace: 'pre', paddingLeft: 4 }}>{lineObj.text}</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
