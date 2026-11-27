import { useLayoutEffect, useRef, useState } from 'react'
import { Info } from 'lucide-react'
import { CodeBlock } from '../design'
import { ScrollTrigger, gsap } from '../design/motion'
import { PREVIEW_NOTE, WORKFLOW } from './content'
import { sceneState } from './sceneState'
import styles from './WorkflowStory.module.css'

/** Illustrative console output per stage, with fictional sample data (acme/api). */
const EXAMPLES: Record<string, { label: string; code: string; diff?: boolean }> = {
  failure: {
    label: 'webhook · sample',
    code: 'POST /v1/webhooks/github\nx-github-event: workflow_run\nx-hub-signature-256: sha256=9f2c…\n→ signature verified',
  },
  evidence: {
    label: 'github api · sample',
    code: 'GET /repos/acme/api/actions/runs/1842\nconclusion: failure   ✓ matches webhook\nlog: failing step "Lint" isolated\nsecrets redacted before analysis',
  },
  diagnosis: {
    label: 'diagnosis · sample',
    code: "category: lint\ntool: ruff\nrule: F401  'os' imported but unused\nlocation: api/handlers.py:3\nschema: valid",
  },
  proposal: {
    label: 'api/handlers.py · sample',
    code: '--- a/api/handlers.py\n+++ b/api/handlers.py\n@@ -1,4 +1,3 @@\n-import os\n import json\n from api import app',
    diff: true,
  },
  validation: {
    label: 'sandbox · sample',
    code: '$ ruff check .\nAll checks passed!\nexit 0 · network none · read-only root',
  },
  approval: {
    label: 'slack · sample',
    code: 'Akesis · fix ready for acme/api\nruff F401 · 1 file · validated\n[ Approve ]  [ Reject ]\n✓ approved by an authorized reviewer',
  },
  'pull-request': {
    label: 'github · sample',
    code: 'branch: akesis/fix-inc-8f2c\npull request opened for review\nmerge: left to your team',
  },
}

export function WorkflowStory({ rich }: { rich: boolean }) {
  const pinRef = useRef<HTMLDivElement>(null)
  const [active, setActive] = useState(0)

  useLayoutEffect(() => {
    if (!rich || !pinRef.current) return
    const pinned = pinRef.current
    const ctx = gsap.context(() => {
      ScrollTrigger.create({
        trigger: pinned,
        start: 'top top+=64',
        end: () => `+=${window.innerHeight * 0.55 * (WORKFLOW.length - 1)}`,
        pin: true,
        scrub: true,
        invalidateOnRefresh: true,
        // Tell the 3D scene where this pin runs, so it can follow the seven steps.
        onRefresh: (self) => {
          sceneState.pins.workflow = { start: self.start, end: self.end }
        },
        onUpdate: (self) => {
          const index = Math.min(WORKFLOW.length - 1, Math.floor(self.progress * WORKFLOW.length))
          setActive((current) => (current === index ? current : index))
          pinned.style.setProperty('--progress', self.progress.toFixed(4))
        },
      })
    }, pinned)
    return () => {
      ctx.revert()
      pinned.style.removeProperty('--progress')
      delete sceneState.pins.workflow
    }
  }, [rich])

  return (
    <section id="workflow" className={styles.section} aria-labelledby="workflow-title" data-scene="workflow">
      <div className="ak-container">
        <header className={styles.header}>
          <p className="ak-eyebrow">How it works</p>
          <h2 id="workflow-title" className="ak-heading">
            From failed run to reviewed fix, in seven verified steps.
          </h2>
        </header>

        <div ref={pinRef} className={[styles.story, rich ? styles.rich : ''].join(' ')}>
          <ol className={styles.timeline}>
            {WORKFLOW.map((stage, index) => {
              const example = EXAMPLES[stage.id]
              const isActive = rich ? index === active : true
              const isDone = rich && index < active
              return (
                <li
                  key={stage.id}
                  className={styles.stage}
                  data-active={isActive || undefined}
                  data-done={isDone || undefined}
                >
                  <span className={styles.node} aria-hidden="true" />
                  <div className={styles.content}>
                    <h3 className={styles.title}>{stage.title}</h3>
                    <div className={styles.detail}>
                      <p className={styles.body}>{stage.body}</p>
                      <ul className={styles.tags} aria-label="Details">
                        {stage.tags.map((tag) => <li key={tag} className={styles.tag}>{tag}</li>)}
                      </ul>
                    </div>
                  </div>
                  {example && (
                    <div className={styles.example}>
                      <span className={styles.exampleNote}>Illustrative example</span>
                      <CodeBlock code={example.code} label={example.label} diff={example.diff} />
                    </div>
                  )}
                </li>
              )
            })}
          </ol>
        </div>

        <p className={styles.note}>
          <Info size={16} aria-hidden="true" />
          {PREVIEW_NOTE}
        </p>
      </div>
    </section>
  )
}
