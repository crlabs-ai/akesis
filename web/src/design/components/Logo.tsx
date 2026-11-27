import { MARK_PATH, MARK_VIEWBOX } from '../logoMark'
import styles from './Logo.module.css'

export interface LogoProps {
  /** Show the wordmark next to the mark. */
  wordmark?: boolean
  size?: number
}

export function Logo({ wordmark = true, size = 28 }: LogoProps) {
  return (
    <span className={styles.logo}>
      <svg width={size * 1.28} height={size} viewBox={MARK_VIEWBOX} aria-hidden="true" focusable="false">
        <path d={MARK_PATH} fill="currentColor" fillRule="evenodd" />
      </svg>
      {wordmark ? <span className={styles.wordmark}>Akesis</span> : <span className="ak-visually-hidden">Akesis</span>}
    </span>
  )
}
