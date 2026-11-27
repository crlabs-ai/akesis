import React from 'react'
import { Link } from 'react-router-dom'
import { Loader2 } from 'lucide-react'
import styles from './Button.module.css'

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger'
export type ButtonSize = 'sm' | 'md' | 'lg'

interface Common {
  variant?: ButtonVariant
  size?: ButtonSize
  /** Icon shown after the label (an arrow, for example). Decorative. */
  trailingIcon?: React.ReactNode
  leadingIcon?: React.ReactNode
}

function buttonClass(variant: ButtonVariant = 'primary', size: ButtonSize = 'md', extra?: string): string {
  return [styles.button, styles[variant], styles[size], extra].filter(Boolean).join(' ')
}

function Content({ leadingIcon, trailingIcon, children }: Common & { children: React.ReactNode }) {
  return (
    <>
      {leadingIcon && <span className={styles.icon} aria-hidden="true">{leadingIcon}</span>}
      <span>{children}</span>
      {trailingIcon && <span className={styles.icon} aria-hidden="true">{trailingIcon}</span>}
    </>
  )
}

export interface ButtonProps extends Common, React.ButtonHTMLAttributes<HTMLButtonElement> {
  loading?: boolean
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant, size, loading = false, leadingIcon, trailingIcon, className, children, disabled, type = 'button', ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      className={buttonClass(variant, size, className)}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...rest}
    >
      <Content
        leadingIcon={loading ? <Loader2 className={styles.spin} size={16} /> : leadingIcon}
        trailingIcon={trailingIcon}
      >
        {children}
      </Content>
    </button>
  )
})

export interface ButtonLinkProps extends Common, Omit<React.AnchorHTMLAttributes<HTMLAnchorElement>, 'href'> {
  /**
   * An in-app route ("/app") renders a router Link; an http(s) URL renders an external anchor; an
   * in-page anchor ("#security") renders a plain link so the browser scrolls to it.
   */
  to: string
}

export function ButtonLink({ to, variant, size, leadingIcon, trailingIcon, className, children, ...rest }: ButtonLinkProps) {
  const cls = buttonClass(variant, size, className)
  const content = <Content leadingIcon={leadingIcon} trailingIcon={trailingIcon}>{children}</Content>
  if (to.startsWith('#')) {
    return (
      <a href={to} className={cls} {...rest}>
        {content}
      </a>
    )
  }
  if (/^https?:\/\//.test(to)) {
    return (
      <a href={to} className={cls} target="_blank" rel="noopener noreferrer" {...rest}>
        {content}
      </a>
    )
  }
  return (
    <Link to={to} className={cls} {...rest}>
      {content}
    </Link>
  )
}
