import { ArrowUpRight, Check, Lock } from 'lucide-react'
import { ButtonLink } from '../design'
import { SPOTLIGHTS } from './content'
import styles from './Spotlights.module.css'

/*
 * Alternating feature rows, each with a wireframe illustration: muted outlines on black with one
 * element lit in blue, the thing the row is about. All art is decorative (aria-hidden) and drawn
 * here in HTML/CSS; nothing is a screenshot or real data.
 */

/** A CI log; the failing line is the one that glows. */
function EvidenceArt() {
  return (
    <div className={styles.device}>
      <div className={styles.screen}>
        <span className={styles.dots}><i /><i /><i /></span>
        <div className={styles.tabs}><span className={styles.tabActive} /><span className={styles.tab} /></div>
        {[62, 48, 70, 40].map((w, i) => (
          <div key={i} className={styles.logRow}><span className={styles.bar} style={{ width: `${w}%` }} /></div>
        ))}
        <div className={`${styles.logRow} ${styles.lit}`}>
          <span className={styles.mono}>ruff F401 · api/handlers.py:3</span>
          <span className={styles.pill}>failing step</span>
        </div>
        {[55, 35].map((w, i) => (
          <div key={i} className={styles.logRow}><span className={styles.bar} style={{ width: `${w}%` }} /></div>
        ))}
      </div>
    </div>
  )
}

/** A diff; the single removed line is lit. */
function DiffArt() {
  return (
    <div className={styles.device}>
      <div className={styles.screen}>
        <span className={styles.dots}><i /><i /><i /></span>
        <div className={styles.diffHead}><span className={styles.bar} style={{ width: '38%' }} /></div>
        <div className={`${styles.diffRow} ${styles.lit}`}>
          <span className={styles.sign}>−</span>
          <span className={styles.mono}>import os</span>
        </div>
        {[30, 44, 0, 52, 36].map((w, i) => (
          <div key={i} className={styles.diffRow}>
            <span className={styles.sign} />
            {w > 0 && <span className={styles.bar} style={{ width: `${w}%` }} />}
          </div>
        ))}
        <div className={styles.diffFoot}><span className={styles.pill}>1 file · 1 line</span></div>
      </div>
    </div>
  )
}

/** A grid of containers; one is sealed and lit, fed by a single signed line. */
function SandboxArt() {
  // 4 × 3 keys, one of them double-width, like a keyboard of throwaway containers.
  const keys = ['', '', 'wide', '', '', 'lit', '', '', '', '', 'wide', '']
  return (
    <div className={styles.keys}>
      <span className={styles.wire} />
      {keys.map((kind, i) => (
        <span key={i} className={[styles.key, kind === 'wide' ? styles.keyWide : '', kind === 'lit' ? styles.keyLit : ''].join(' ')}>
          {kind === 'lit' && <Lock size={18} />}
        </span>
      ))}
    </div>
  )
}

/** An approval card; Approve is lit and the pointer is on it. */
function ApprovalArt() {
  return (
    <div className={styles.device}>
      <div className={`${styles.screen} ${styles.approval}`}>
        <span className={styles.dots}><i /><i /><i /></span>
        <span className={styles.bar} style={{ width: '46%' }} />
        <span className={styles.bar} style={{ width: '64%', opacity: 0.5 }} />
        <span className={styles.bar} style={{ width: '38%', opacity: 0.5 }} />
        <div className={styles.actions}>
          <span className={`${styles.action} ${styles.lit}`}><Check size={14} /> Approve</span>
          <span className={styles.action}>Reject</span>
          <svg className={styles.cursor} viewBox="0 0 24 24" width="26" height="26">
            <path d="M5 3l14 8-6 1.5L10 19z" fill="#000" stroke="#fff" strokeWidth="1.5" strokeLinejoin="round" />
          </svg>
        </div>
      </div>
    </div>
  )
}

const ART = { evidence: EvidenceArt, diff: DiffArt, sandbox: SandboxArt, approval: ApprovalArt }

export function Spotlights() {
  return (
    <section className={styles.section} aria-labelledby="spotlights-title">
      <h2 id="spotlights-title" className="ak-visually-hidden">What Akesis does, step by step</h2>
      <div className={`ak-container ${styles.list}`}>
        {SPOTLIGHTS.map((spot, index) => {
          const Art = ART[spot.id]
          return (
            <article key={spot.id} className={styles.row} data-flip={index % 2 === 1 || undefined} data-scene="spotlight">
              <div className={styles.art} aria-hidden="true" data-reveal>
                <Art />
              </div>
              <div className={styles.copy} data-reveal>
                <h3 className={styles.title}>{spot.title}</h3>
                <p className={styles.body}>{spot.body}</p>
                <ButtonLink to={spot.cta.to} size="sm" trailingIcon={<ArrowUpRight size={14} />}>
                  {spot.cta.label}
                </ButtonLink>
              </div>
            </article>
          )
        })}
      </div>
    </section>
  )
}
