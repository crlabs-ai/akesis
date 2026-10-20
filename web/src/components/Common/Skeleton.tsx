import React from 'react'

// Neutral graphite placeholders shaped like the real layouts. They render only
// while the owning view is genuinely loading, never in place of an error or empty state.

interface TableSkeletonProps {
  columns: number[] // relative column widths, e.g. [2, 3, 2, 1]
  rows?: number
  label: string
}

export const TableSkeleton: React.FC<TableSkeletonProps> = ({ columns, rows = 8, label }) => (
  <div className="sk-table" role="status" aria-live="polite" aria-label={label}>
    <span className="sk-sr">{label}</span>
    <div className="sk-table-head" style={{ gridTemplateColumns: columns.map((c) => `${c}fr`).join(' ') }}>
      {columns.map((_, i) => (
        <span key={i} className="sk-bar sk-bar-head" />
      ))}
    </div>
    {Array.from({ length: rows }).map((_, r) => (
      <div key={r} className="sk-table-row" style={{ gridTemplateColumns: columns.map((c) => `${c}fr`).join(' ') }}>
        {columns.map((_, c) => (
          <span key={c} className={`sk-bar ${c === 0 ? 'sk-bar-strong' : ''}`} style={{ width: `${55 + ((r * 7 + c * 13) % 35)}%` }} />
        ))}
      </div>
    ))}
  </div>
)

interface BlockSkeletonProps {
  lines?: number
  label: string
}

export const BlockSkeleton: React.FC<BlockSkeletonProps> = ({ lines = 4, label }) => (
  <div className="sk-block" role="status" aria-live="polite" aria-label={label}>
    <span className="sk-sr">{label}</span>
    {Array.from({ length: lines }).map((_, i) => (
      <span key={i} className="sk-bar" style={{ width: `${92 - i * 14}%` }} />
    ))}
  </div>
)
