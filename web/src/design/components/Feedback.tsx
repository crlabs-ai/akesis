import React from 'react'
import { AlertOctagon } from 'lucide-react'
import styles from './Feedback.module.css'

export interface SkeletonProps {
  width?: number | string
  height?: number | string
  radius?: 'sm' | 'md' | 'pill'
  className?: string
}

/** A placeholder block. Wrap groups in an element with aria-busy so screen readers hear "loading". */
export function Skeleton({ width = '100%', height = 14, radius = 'sm', className }: SkeletonProps) {
  return (
    <span
      className={[styles.skeleton, styles[`r_${radius}`], className].filter(Boolean).join(' ')}
      style={{ width, height }}
      aria-hidden="true"
    />
  )
}

interface StateProps {
  icon?: React.ReactNode
  title: string
  children?: React.ReactNode
  /** Primary action, such as a retry button or a setup link. */
  action?: React.ReactNode
}

/**
 * Nothing to show yet, or a capability that is not available. Says so plainly; it never stands in
 * for data that does not exist.
 */
export function EmptyState({ icon, title, children, action }: StateProps) {
  return (
    <div className={styles.state}>
      {icon && <div className={styles.icon} aria-hidden="true">{icon}</div>}
      <h3 className={styles.title}>{title}</h3>
      {children && <div className={styles.body}>{children}</div>}
      {action && <div className={styles.action}>{action}</div>}
    </div>
  )
}

/** Something failed. Announced to assistive technology as an alert. */
export function ErrorState({ icon, title, children, action }: StateProps) {
  return (
    <div className={[styles.state, styles.error].join(' ')} role="alert">
      <div className={styles.icon} aria-hidden="true">{icon ?? <AlertOctagon size={20} />}</div>
      <h3 className={styles.title}>{title}</h3>
      {children && <div className={styles.body}>{children}</div>}
      {action && <div className={styles.action}>{action}</div>}
    </div>
  )
}
