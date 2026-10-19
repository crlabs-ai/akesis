import React from 'react'
import {
  AlertCircle,
  AlertTriangle,
  CheckCircle2,
  Clock,
  HelpCircle,
  ShieldCheck,
  XCircle,
} from 'lucide-react'

interface StatusBadgeProps {
  status: string | null | undefined
  type?: 'pipeline' | 'validation' | 'approval' | 'category' | 'conclusion' | 'stage'
}

export const StatusBadge: React.FC<StatusBadgeProps> = ({ status, type = 'pipeline' }) => {
  if (!status) {
    return <span className="badge badge-neutral">—</span>
  }

  const s = status.toLowerCase()

  // Validation Statuses
  if (type === 'validation') {
    if (s === 'passed') {
      return (
        <span className="badge badge-success">
          <CheckCircle2 size={11} /> PASSED
        </span>
      )
    }
    if (s === 'failed' || s === 'patch_rejected' || s === 'infrastructure_error') {
      return (
        <span className="badge badge-danger">
          <XCircle size={11} /> {s.toUpperCase()}
        </span>
      )
    }
    if (s === 'timed_out') {
      return (
        <span className="badge badge-warning">
          <Clock size={11} /> TIMED OUT
        </span>
      )
    }
    return <span className="badge badge-neutral">{status.toUpperCase()}</span>
  }

  // Approval Statuses
  if (type === 'approval') {
    if (s === 'approved' || s === 'auto_approved') {
      return (
        <span className="badge badge-success">
          <ShieldCheck size={11} /> {s.toUpperCase()}
        </span>
      )
    }
    if (s === 'rejected' || s === 'denied') {
      return (
        <span className="badge badge-danger">
          <XCircle size={11} /> REJECTED
        </span>
      )
    }
    if (s === 'pending' || s === 'awaiting_approval') {
      return (
        <span className="badge badge-warning">
          <Clock size={11} /> PENDING
        </span>
      )
    }
    return <span className="badge badge-neutral">{status.toUpperCase()}</span>
  }

  // Pipeline Statuses
  if (type === 'pipeline') {
    if (s === 'completed') {
      return (
        <span className="badge badge-success">
          <CheckCircle2 size={11} /> COMPLETED
        </span>
      )
    }
    if (s === 'failed' || s === 'rejected') {
      return (
        <span className="badge badge-danger">
          <XCircle size={11} /> {s.toUpperCase()}
        </span>
      )
    }
    if (s === 'awaiting_approval' || s === 'validating' || s === 'analyzing' || s === 'proposing') {
      return (
        <span className="badge badge-warning">
          <Clock size={11} /> {s.replace('_', ' ').toUpperCase()}
        </span>
      )
    }
    if (s === 'quarantined') {
      return (
        <span className="badge badge-info">
          <AlertTriangle size={11} /> QUARANTINED
        </span>
      )
    }
    return <span className="badge badge-neutral">{s.replace('_', ' ').toUpperCase()}</span>
  }

  // Categories
  if (type === 'category') {
    return <span className="badge badge-info">{status.replace('_', ' ')}</span>
  }

  // Conclusions
  if (type === 'conclusion') {
    if (s === 'success') {
      return (
        <span className="badge badge-success">
          <CheckCircle2 size={11} /> SUCCESS
        </span>
      )
    }
    if (s === 'failure') {
      return (
        <span className="badge badge-danger">
          <AlertCircle size={11} /> FAILURE
        </span>
      )
    }
    return <span className="badge badge-neutral">{s.toUpperCase()}</span>
  }

  // Stages: readable neutral labels for the persisted stage value; no invented states.
  if (type === 'stage') {
    const label = s.replace(/_/g, ' ')
    const tone =
      s === 'completed' ? 'badge-success' : s === 'failed' ? 'badge-danger' : s === 'awaiting_approval' ? 'badge-warning' : 'badge-neutral'
    return <span className={`badge ${tone}`}>{label.charAt(0).toUpperCase() + label.slice(1)}</span>
  }

  // Fallback
  return (
    <span className="badge badge-neutral">
      <HelpCircle size={11} /> {status}
    </span>
  )
}
