/**
 * State shared between the page and the 3D scene. A plain mutable object, read every frame inside
 * the canvas, so scrolling never re-renders React. Kept apart from the scene module so the page
 * can write to it without pulling three.js into the main bundle.
 */
export const sceneState = {
  /**
   * Scroll range (in px) of each pinned section, keyed by its `data-scene` name. Written by the
   * section's ScrollTrigger, so the scene can follow the pinned steps and slides exactly.
   */
  pins: {} as Record<string, { start: number; end: number }>,
  /** Set once the model, textures and shaders are ready and the first frame can be drawn. */
  ready: false,
}

const READY_EVENT = 'ak:scene-ready'

/** Called by the scene when it can draw its first frame. */
export function markSceneReady(): void {
  if (sceneState.ready) return
  sceneState.ready = true
  window.dispatchEvent(new Event(READY_EVENT))
}

/** Runs `callback` once the scene is ready, or after `timeoutMs` at the latest. Returns a cancel. */
export function whenSceneReady(callback: () => void, timeoutMs: number): () => void {
  if (sceneState.ready) {
    callback()
    return () => {}
  }
  let done = false
  const run = () => {
    if (done) return
    done = true
    window.removeEventListener(READY_EVENT, run)
    window.clearTimeout(timer)
    callback()
  }
  const timer = window.setTimeout(run, timeoutMs)
  window.addEventListener(READY_EVENT, run)
  return () => {
    done = true
    window.removeEventListener(READY_EVENT, run)
    window.clearTimeout(timer)
  }
}

/** WebGL2 is required for the scene; everything else gets the static glow. */
export function supportsWebGL(): boolean {
  try {
    return !!document.createElement('canvas').getContext('webgl2')
  } catch {
    return false
  }
}
