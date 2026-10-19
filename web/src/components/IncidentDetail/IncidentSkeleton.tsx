import React from 'react'

export const IncidentSkeleton: React.FC = () => {
  return (
    <div className="incident-skeleton incident-workspace" aria-label="Loading incident workspace">
      {/* Header bar skeleton */}
      <div className="skeleton-box sk-bar ws-skel-header" />

      {/* Stage graph skeleton */}
      <div className="skeleton-box sk-bar ws-skel-strip" />

      {/* 3-Column Workstation Layout Skeleton */}
      <div className="incident-workstation">
        {/* Left column */}
        <div className="workstation-column workstation-left">
          <div className="skeleton-box sk-bar ws-skel-fill" />
          <div className="skeleton-box sk-bar ws-skel-fill" />
        </div>

        {/* Center column (large diff workspace) */}
        <div className="workstation-column workstation-center">
          <div className="skeleton-box sk-bar ws-skel-line" />
          <div className="skeleton-box sk-bar ws-skel-fill" />
        </div>

        {/* Right column */}
        <div className="workstation-column workstation-right">
          <div className="skeleton-box sk-bar ws-skel-fill" />
        </div>
      </div>

      {/* Docked terminal skeleton */}
      <div className="skeleton-box sk-bar ws-skel-terminal" />
    </div>
  )
}
