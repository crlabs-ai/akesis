import { useLayoutEffect, useRef } from 'react'
import { ArrowUpRight, Boxes, BrainCircuit, CheckCheck, FileDiff, GitPullRequest, Lock, MessageSquare, ScanSearch, ShieldCheck, TerminalSquare, UserCheck } from 'lucide-react'
import { Badge, ButtonLink, Card, CodeBlock, Logo } from '../design'
import { gsap } from '../design/motion'
import { sceneState } from './sceneState'
import { CAPABILITIES, CONSOLE_PATH, INTEGRATIONS, NAV_LINKS, PRINCIPLES, PROBLEMS, REPOSITORY_URL, SANDBOX_COMMAND } from './content'
import { GitHubMark } from './icons'
import styles from './Sections.module.css'

function SectionHeader({ id, eyebrow, title, lead }: { id: string; eyebrow: string; title: string; lead?: string }) {
  return (
    <header className={styles.header} data-reveal>
      <p className="ak-eyebrow">{eyebrow}</p>
      <h2 id={id} className="ak-heading">{title}</h2>
      {lead && <p className="ak-lead">{lead}</p>}
    </header>
  )
}

export function Problem() {
  return (
    <section className={styles.section} aria-labelledby="problem-title" data-scene="problem">
      <div className="ak-container">
        <SectionHeader
          id="problem-title"
          eyebrow="Why it matters"
          title="A red build stops more than the pipeline."
          lead="Most CI failures are small. Tracing the cause, writing the fix and proving it still pulls an engineer out of their work."
        />
        <div className={styles.grid3}>
          {PROBLEMS.map((problem) => (
            <Card key={problem.title} as="article" data-reveal>
              <h3 className={styles.cardTitle}>{problem.title}</h3>
              <p className={styles.cardBody}>{problem.body}</p>
            </Card>
          ))}
        </div>
      </div>
    </section>
  )
}

const CAPABILITY_ICONS = [ScanSearch, BrainCircuit, FileDiff, Boxes, CheckCheck, TerminalSquare]

/** In rich mode the section pins and the cards slide sideways as the page scrolls. */
export function Capabilities({ rich = false }: { rich?: boolean }) {
  const section = useRef<HTMLElement>(null)
  const track = useRef<HTMLUListElement>(null)

  useLayoutEffect(() => {
    const el = track.current
    if (!rich || !section.current || !el) return
    const distance = () => Math.max(0, el.scrollWidth - (el.parentElement?.clientWidth ?? 0))
    const ctx = gsap.context(() => {
      gsap.to(el, {
        x: () => -distance(),
        ease: 'none',
        scrollTrigger: {
          trigger: section.current,
          start: 'top top',
          end: () => `+=${distance()}`,
          pin: true,
          scrub: 0.6,
          invalidateOnRefresh: true,
          // Tell the 3D scene where the slides run, so it can sweep alongside them.
          onRefresh: (self) => {
            sceneState.pins.capabilities = { start: self.start, end: self.end }
          },
        },
      })
    }, section)
    return () => {
      ctx.revert()
      delete sceneState.pins.capabilities
    }
  }, [rich])

  return (
    <section
      ref={section}
      id="capabilities"
      className={styles.section}
      aria-labelledby="capabilities-title"
      data-scene="capabilities"
      data-rich={rich || undefined}
    >
      <div className={`ak-container ${styles.rail}`}>
        <SectionHeader
          id="capabilities-title"
          eyebrow="Capabilities"
          title="Built for the work between failure and fix."
          lead="Python projects on GitHub Actions: lint and formatting, import and dependency errors, and failing pytest assertions."
        />
        <ul ref={track} className={`${styles.grid3} ${styles.track}`}>
          {CAPABILITIES.map((capability, index) => {
            const Icon = CAPABILITY_ICONS[index]
            return (
              <Card key={capability.title} as="li" interactive data-reveal>
                <span className={styles.iconTile} aria-hidden="true"><Icon size={20} /></span>
                <h3 className={styles.cardTitle}>{capability.title}</h3>
                <p className={styles.cardBody}>{capability.body}</p>
              </Card>
            )
          })}
        </ul>
      </div>
    </section>
  )
}

const PRINCIPLE_ICONS = [ShieldCheck, Lock, UserCheck, GitPullRequest]

export function Security() {
  return (
    <section id="security" className={styles.section} aria-labelledby="security-title" data-scene="security">
      <div className={`ak-container ${styles.split}`}>
        <div>
          <SectionHeader
            id="security-title"
            eyebrow="Security"
            title="Trust is designed in, not bolted on."
          />
          <ul className={styles.principles}>
            {PRINCIPLES.map((principle, index) => {
              const Icon = PRINCIPLE_ICONS[index]
              return (
                <li key={principle.title} className={styles.principle} data-reveal>
                  <span className={styles.principleIcon} aria-hidden="true"><Icon size={18} /></span>
                  <div>
                    <h3 className={styles.principleTitle}>{principle.title}</h3>
                    <p className={styles.cardBody}>{principle.body}</p>
                  </div>
                </li>
              )
            })}
          </ul>
        </div>
        <div className={styles.terminal} data-reveal>
          <p className={styles.terminalNote}>The validation sandbox, as Akesis runs it (abridged)</p>
          <CodeBlock code={SANDBOX_COMMAND} label="sandbox_runner.py" />
        </div>
      </div>
    </section>
  )
}

export function Integrations() {
  return (
    <section className={styles.section} aria-labelledby="integrations-title" data-scene="integrations">
      <div className="ak-container">
        <SectionHeader id="integrations-title" eyebrow="Integrations" title="Fits the tools your team already uses." />
        <ul className={styles.grid2}>
          {INTEGRATIONS.map((integration) => (
            <Card key={integration.name} as="li" data-reveal>
              <div className={styles.integrationHead}>
                <span className={styles.iconTile} aria-hidden="true">
                  {integration.name === 'GitHub' ? <GitHubMark size={20} /> : <MessageSquare size={20} />}
                </span>
                <h3 className={styles.cardTitle}>{integration.name}</h3>
                <Badge tone="success" icon>{integration.status}</Badge>
              </div>
              <p className={styles.cardBody}>{integration.body}</p>
            </Card>
          ))}
        </ul>
      </div>
    </section>
  )
}

export function FinalCta() {
  return (
    <section className={styles.cta} aria-labelledby="cta-title" data-scene="cta">
      <div className={`ak-container ${styles.ctaInner}`} data-reveal>
        <h2 id="cta-title" className="ak-display">
          Keep shipping. <span className={styles.accent}>Let Akesis handle the red builds.</span>
        </h2>
        <p className="ak-lead">Akesis is in private preview. Explore the operator console or read the source.</p>
        <div className={styles.ctaActions}>
          <ButtonLink to={CONSOLE_PATH} size="lg" trailingIcon={<ArrowUpRight size={16} />}>Open the console</ButtonLink>
          <ButtonLink to={REPOSITORY_URL} size="lg" variant="secondary" leadingIcon={<GitHubMark size={18} />}>
            View on GitHub
          </ButtonLink>
        </div>
      </div>
    </section>
  )
}

export function SiteFooter() {
  return (
    <footer className={styles.footer}>
      <div className={`ak-container ${styles.footerInner}`}>
        <Logo />
        <nav aria-label="Footer" className={styles.footerLinks}>
          {NAV_LINKS.map((link) => <a key={link.href} href={link.href}>{link.label}</a>)}
          <a href={REPOSITORY_URL} target="_blank" rel="noopener noreferrer">GitHub</a>
        </nav>
        <p className={styles.footerNote}>Private preview · Akesis never merges a pull request on its own.</p>
      </div>
    </footer>
  )
}
