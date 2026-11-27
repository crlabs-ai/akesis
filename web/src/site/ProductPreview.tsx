import { useState } from 'react'
import { Badge, CodeBlock, StatusDot, Tabs, type Tone } from '../design'
import styles from './ProductPreview.module.css'

/*
 * A vignette of the incident view, rendered with the real design-system components and fictional
 * sample data. It is labelled as an illustrative demo, visibly and for assistive technology, and
 * shows no metrics.
 */

const STAGES: { label: string; tone: Tone; state: string }[] = [
  { label: 'Evidence', tone: 'success', state: 'verified' },
  { label: 'Diagnosis', tone: 'success', state: 'complete' },
  { label: 'Proposal', tone: 'success', state: 'ready' },
  { label: 'Validation', tone: 'success', state: 'passed' },
  { label: 'Approval', tone: 'warning', state: 'waiting' },
  { label: 'Pull request', tone: 'neutral', state: 'not started' },
]

const DIFF = `--- a/api/handlers.py
+++ b/api/handlers.py
@@ -1,6 +1,5 @@
-import os
 import json

 from api import app`

/** `bare`: no section header, for use directly under the hero (as the reference shows its product). */
export function ProductPreview({ bare = false }: { bare?: boolean }) {
  const [tab, setTab] = useState('diagnosis')

  return (
    <section
      id="preview"
      className={[styles.section, bare ? styles.bare : ''].join(' ')}
      aria-labelledby="preview-title"
    >
      <div className="ak-container">
        <header className={bare ? 'ak-visually-hidden' : styles.header} data-reveal>
          <p className="ak-eyebrow">Product preview</p>
          <h2 id="preview-title" className="ak-heading">One incident, every step in view.</h2>
        </header>

        <figure
          className={styles.window}
          aria-label="Illustrative demo of the Akesis incident view, with sample data"
          {...(bare ? { 'data-hero-window': '' } : { 'data-reveal': '' })}
        >
          <div className={styles.chrome}>
            <span className={styles.lights} aria-hidden="true"><i /><i /><i /></span>
            <span className={styles.address}>akesis · incidents / inc_8f2c</span>
            <Badge tone="info">Illustrative demo · sample data</Badge>
          </div>

          <div className={styles.body}>
            <div className={styles.incidentHead}>
              <div>
                <p className={styles.meta}>acme/api · main · run 1842</p>
                <h3 className={styles.incidentTitle}>Lint failed: ruff F401, unused import</h3>
              </div>
              <Badge tone="warning" icon>Awaiting approval</Badge>
            </div>

            <ol className={styles.stages} aria-label="Pipeline stages">
              {STAGES.map((stage) => (
                <li key={stage.label} className={styles.stage}>
                  <span className={styles.stageName}>{stage.label}</span>
                  <StatusDot tone={stage.tone} label={stage.state} />
                </li>
              ))}
            </ol>

            <Tabs
              label="Incident details"
              value={tab}
              onChange={setTab}
              items={[
                {
                  id: 'diagnosis',
                  label: 'Diagnosis',
                  panel: (
                    <div className={styles.panel}>
                      <p>
                        The <code>Lint</code> step failed because <code>ruff</code> reported{' '}
                        <code>F401</code>: <code>os</code> is imported in <code>api/handlers.py</code> but never used.
                      </p>
                      <p className={styles.muted}>Category: lint · Evidence: verified run log, redacted</p>
                    </div>
                  ),
                },
                { id: 'diff', label: 'Proposed diff', panel: <CodeBlock code={DIFF} label="api/handlers.py" diff /> },
                {
                  id: 'validation',
                  label: 'Validation',
                  panel: <CodeBlock code={'$ ruff check .\nAll checks passed!\n\nexit 0 · network none · read-only root'} label="sandbox" />,
                },
              ]}
            />
          </div>
        </figure>
      </div>
    </section>
  )
}
