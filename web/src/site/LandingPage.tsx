import { Component, Suspense, lazy, useEffect, useLayoutEffect, useMemo, useRef, type ReactNode } from 'react'
import 'lenis/dist/lenis.css'
import { RICH_MOTION_QUERY, ScrollTrigger, SplitText, gsap, usePrefersReducedMotion, useRichMotion, useSmoothScroll } from '../design/motion'
import { Hero } from './Hero'
import { ProductPreview } from './ProductPreview'
import { Capabilities, FinalCta, Integrations, Problem, Security, SiteFooter } from './Sections'
import { SiteNav } from './SiteNav'
import { Spotlights } from './Spotlights'
import { WorkflowStory } from './WorkflowStory'
import { supportsWebGL } from './sceneState'

const loadScene = () => import('./ParticleField')
// Start fetching the 3D chunk with the page itself rather than after the first render, wherever the
// scene will run, so the scene is ready by the time the hero's entrance plays.
if (typeof window !== 'undefined' && window.matchMedia?.(RICH_MOTION_QUERY).matches && supportsWebGL()) void loadScene()
const ParticleField = lazy(loadScene)

/** The 3D backdrop is decoration: if it fails to load or render, the page carries on without it. */
class SceneBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() {
    return { failed: true }
  }
  render() {
    return this.state.failed ? null : this.props.children
  }
}

export default function LandingPage() {
  const root = useRef<HTMLDivElement>(null)
  const reduced = usePrefersReducedMotion()
  const rich = useRichMotion()
  useSmoothScroll(rich)
  const scene = useMemo(() => rich && supportsWebGL(), [rich])

  useEffect(() => {
    document.title = 'Akesis · Broken builds, fixed with proof'
  }, [])

  // Section reveals. CSS never hides content: the start state is set here, by script, before first
  // paint, so if scripts or animation fail everything stays visible. Nothing runs under reduced motion.
  useLayoutEffect(() => {
    if (reduced || !root.current) return
    const ctx = gsap.context(() => {
      gsap.set('[data-reveal]', { y: 28, opacity: 0 })
      ScrollTrigger.batch('[data-reveal]', {
        start: 'top 88%',
        once: true,
        onEnter: (elements) =>
          gsap.to(elements, { y: 0, opacity: 1, duration: 0.9, ease: 'power3.out', stagger: 0.1, overwrite: true }),
      })
      // Section headings rise line by line out of a mask. SplitText keeps the heading's accessible
      // name on the element itself (aria: auto), so screen readers hear one sentence.
      for (const heading of gsap.utils.toArray<HTMLElement>('main section:not([aria-labelledby="hero-title"]) h2')) {
        const split = SplitText.create(heading, { type: 'lines', mask: 'lines', aria: 'auto' })
        gsap.from(split.lines, {
          yPercent: 110,
          duration: 0.9,
          ease: 'power3.out',
          stagger: 0.08,
          scrollTrigger: { trigger: heading, start: 'top 88%', once: true },
        })
      }
    }, root)
    return () => ctx.revert()
  }, [reduced])

  return (
    <div ref={root} className="ak">
      <a href="#main" className="ak-skip">Skip to content</a>
      {scene && (
        <SceneBoundary>
          <Suspense fallback={null}>
            <ParticleField />
          </Suspense>
        </SceneBoundary>
      )}
      <SiteNav />
      <main id="main" tabIndex={-1} className="ak-layer">
        <Hero waitForScene={scene} />
        <ProductPreview bare />
        <Problem />
        <Spotlights />
        <WorkflowStory rich={rich} />
        <Capabilities rich={rich} />
        <Security />
        <Integrations />
        <FinalCta />
      </main>
      <div className="ak-layer">
        <SiteFooter />
      </div>
    </div>
  )
}
