import { useLayoutEffect, useRef } from 'react'
import { ArrowUpRight } from 'lucide-react'
import { ButtonLink } from '../design'
import { SplitText, gsap, usePrefersReducedMotion } from '../design/motion'
import { CONSOLE_PATH, HERO, REPOSITORY_URL } from './content'
import { GitHubMark } from './icons'
import { whenSceneReady } from './sceneState'
import styles from './Hero.module.css'

/** Longest the entrance waits for the 3D scene before playing without it. */
const SCENE_WAIT_MS = 1200

/** `waitForScene`: the 3D scene is loading; start the entrance together with it. */
export function Hero({ waitForScene = false }: { waitForScene?: boolean }) {
  const root = useRef<HTMLElement>(null)
  const reduced = usePrefersReducedMotion()

  useLayoutEffect(() => {
    if (reduced || !root.current) return
    // Content is visible by default; GSAP only animates *from* an offset, so nothing stays hidden
    // if scripts or animation fail.
    let cancel = () => {}
    const heading = root.current.querySelector('h1')
    const ctx = gsap.context(() => {
      const title = root.current?.querySelector('h1')
      const split = title ? SplitText.create(title, { type: 'lines', mask: 'lines', linesClass: styles.line, aria: 'auto' }) : null
      // Each line now carries the gradient; the heading's own would paint a ghost of the hidden lines.
      if (split) title?.classList.add(styles.split)
      // The product window lives just below the hero; it lands last, as the scene finishes fading up.
      const window_ = document.querySelector('[data-hero-window]')
      const tl = gsap.timeline({ defaults: { ease: 'power3.out' }, paused: waitForScene })
      tl.from('[data-hero-badge]', { y: 16, opacity: 0, duration: 0.7 }, 0.1)
      if (split) tl.from(split.lines, { yPercent: 110, duration: 1, stagger: 0.1 }, 0.2)
      tl.from('[data-hero-intro]', { y: 20, opacity: 0, duration: 0.9, stagger: 0.1 }, 0.55)
      if (window_) tl.from(window_, { y: 60, opacity: 0, duration: 1.2 }, 0.95)
      if (waitForScene) cancel = whenSceneReady(() => tl.play(), SCENE_WAIT_MS)
    }, root)
    return () => {
      cancel()
      ctx.revert()
      heading?.classList.remove(styles.split)
    }
  }, [reduced, waitForScene])

  return (
    <section ref={root} className={styles.hero} aria-labelledby="hero-title" data-scene="hero">
      <div className={styles.glow} aria-hidden="true" data-hero-mark />
      <div className={`ak-container ${styles.copy}`}>
        <p className={`ak-glass ${styles.badge}`} data-hero-badge>{HERO.badge}</p>
        <h1 id="hero-title" className={`ak-display ${styles.title}`}>
          {HERO.titleLead} <span className={styles.accent}>{HERO.titleAccent}</span>
        </h1>
        <p className={`ak-lead ${styles.lead}`} data-hero-intro>{HERO.lead}</p>
        <div className={styles.ctas} data-hero-intro>
          <ButtonLink to={CONSOLE_PATH} size="lg" trailingIcon={<ArrowUpRight size={16} />}>
            Open the console
          </ButtonLink>
          <ButtonLink to={REPOSITORY_URL} size="lg" variant="secondary" leadingIcon={<GitHubMark size={18} />}>
            View on GitHub
          </ButtonLink>
        </div>
        <p className={styles.assurance} data-hero-intro>{HERO.assurance}</p>
      </div>
    </section>
  )
}
