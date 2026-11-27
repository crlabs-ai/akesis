import React from 'react'
import styles from './Data.module.css'

export interface TableProps extends React.TableHTMLAttributes<HTMLTableElement> {
  /** Every table has a caption; pass visuallyHidden when the surrounding heading already says it. */
  caption: string
  captionHidden?: boolean
}

/** A data table that scrolls horizontally inside its own frame instead of widening the page. */
export function Table({ caption, captionHidden = false, className, children, ...rest }: TableProps) {
  return (
    <div className={styles.frame}>
      <table className={[styles.table, className].filter(Boolean).join(' ')} {...rest}>
        <caption className={captionHidden ? 'ak-visually-hidden' : styles.caption}>{caption}</caption>
        {children}
      </table>
    </div>
  )
}

export interface CodeBlockProps {
  code: string
  /** File name or language label shown in the header. */
  label?: string
  /** Colour unified-diff lines (+ added, - removed, @@ hunk). */
  diff?: boolean
}

function lineKind(line: string): string {
  if (line.startsWith('+++') || line.startsWith('---')) return styles.meta
  if (line.startsWith('@@')) return styles.hunk
  if (line.startsWith('+')) return styles.add
  if (line.startsWith('-')) return styles.del
  return ''
}

export function CodeBlock({ code, label, diff = false }: CodeBlockProps) {
  const lines = code.replace(/\n$/, '').split('\n')
  return (
    <figure className={styles.code}>
      {label && <figcaption className={styles.codeLabel}>{label}</figcaption>}
      <pre className={styles.pre} tabIndex={0}>
        <code>
          {lines.map((line, index) => (
            <span key={index} className={[styles.line, diff ? lineKind(line) : ''].join(' ')}>
              {line || ' '}
              {'\n'}
            </span>
          ))}
        </code>
      </pre>
    </figure>
  )
}
