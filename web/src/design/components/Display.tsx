import React from 'react'
import { AlertTriangle, CheckCircle2, CircleDashed, Info, XCircle } from 'lucide-react'
import styles from './Display.module.css'

export type Tone = 'neutral' | 'accent' | 'success' | 'warning' | 'danger' | 'info'

const TONE_ICON: Record<Tone, React.ElementType | null> = {
  neutral: CircleDashed,
  accent: null,
  success: CheckCircle2,
  warning: AlertTriangle,
  danger: XCircle,
  info: Info,
}

export interface BadgeProps {
  tone?: Tone
  /** Status badges carry an icon so meaning never rests on colour alone. */
  icon?: boolean
  children: React.ReactNode
  className?: string
}

export function Badge({ tone = 'neutral', icon = false, children, className }: BadgeProps) {
  const Icon = icon ? TONE_ICON[tone] : null
  return (
    <span className={[styles.badge, styles[tone], className].filter(Boolean).join(' ')}>
      {Icon && <Icon size={12} aria-hidden="true" />}
      {children}
    </span>
  )
}

export interface StatusDotProps {
  tone: Tone
  /** Always required: the dot is decorative, the label carries the meaning. */
  label: string
  pulse?: boolean
}

export function StatusDot({ tone, label, pulse = false }: StatusDotProps) {
  return (
    <span className={styles.status}>
      <span className={[styles.dot, styles[`dot_${tone}`], pulse ? styles.pulse : ''].join(' ')} aria-hidden="true" />
      <span>{label}</span>
    </span>
  )
}

export function Kbd({ children }: { children: React.ReactNode }) {
  return <kbd className={styles.kbd}>{children}</kbd>
}

export interface CardProps extends React.HTMLAttributes<HTMLElement> {
  as?: 'div' | 'article' | 'section' | 'li'
  /** Interactive cards lift their border on hover. */
  interactive?: boolean
  padding?: 'sm' | 'md' | 'lg'
}

export function Card({ as: Tag = 'div', interactive = false, padding = 'md', className, ...rest }: CardProps) {
  return (
    <Tag
      className={[styles.card, styles[`pad_${padding}`], interactive ? styles.interactive : '', className]
        .filter(Boolean)
        .join(' ')}
      {...rest}
    />
  )
}
