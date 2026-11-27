/**
 * Motion policy, in one place.
 *
 * - GSAP + ScrollTrigger are registered once here. Components build timelines inside
 *   gsap.context() in a layout effect and revert them on unmount, so no tween or trigger outlives
 *   its component.
 * - Lenis smooth scroll runs only where a page opts in, only for fine pointers on wide screens, and
 *   never under prefers-reduced-motion. It is driven by GSAP's ticker so ScrollTrigger stays in sync,
 *   and destroyed on unmount. Native scrolling (keyboard, anchors, find-in-page) keeps working.
 */
import { useEffect, useState } from 'react'
import gsap from 'gsap'
import { ScrollTrigger } from 'gsap/ScrollTrigger'
import { SplitText } from 'gsap/SplitText'
import Lenis from 'lenis'

gsap.registerPlugin(ScrollTrigger, SplitText)

export { gsap, ScrollTrigger, SplitText }

export const MOTION_OK_QUERY = '(prefers-reduced-motion: no-preference)'
/** Rich, pinned scroll storytelling: fine pointer and enough room for it. */
export const RICH_MOTION_QUERY = '(min-width: 960px) and (pointer: fine) and (prefers-reduced-motion: no-preference)'

function matches(query: string): boolean {
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function' && window.matchMedia(query).matches
}

/** Live media-query state. Without matchMedia (old browsers, test DOMs) the query counts as false. */
export function useMediaQuery(query: string): boolean {
  const [state, setState] = useState(() => matches(query))
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return
    const list = window.matchMedia(query)
    const update = () => setState(list.matches)
    update()
    list.addEventListener('change', update)
    return () => list.removeEventListener('change', update)
  }, [query])
  return state
}

/**
 * Motion is opt-in: only an explicit "no-preference" enables it. Where media queries cannot be
 * evaluated at all, this reports reduced motion, so nothing animates by accident.
 */
export function usePrefersReducedMotion(): boolean {
  return !useMediaQuery(MOTION_OK_QUERY)
}

/** True only where pinned, scrubbed scroll animation is appropriate. */
export function useRichMotion(): boolean {
  return useMediaQuery(RICH_MOTION_QUERY)
}

/** Smooth scrolling for the calling page while `enabled`; a no-op otherwise. */
export function useSmoothScroll(enabled: boolean): void {
  useEffect(() => {
    if (!enabled) return
    const lenis = new Lenis({ autoRaf: false, anchors: true, smoothWheel: true, syncTouch: false })
    const tick = (time: number) => lenis.raf(time * 1000)
    lenis.on('scroll', ScrollTrigger.update)
    gsap.ticker.add(tick)
    gsap.ticker.lagSmoothing(0)
    document.documentElement.classList.add('lenis')
    return () => {
      gsap.ticker.remove(tick)
      gsap.ticker.lagSmoothing(500, 33)
      lenis.destroy()
      document.documentElement.classList.remove('lenis')
    }
  }, [enabled])
}
