import '@testing-library/jest-dom/vitest'

// jsdom implements no media queries, but every browser does and GSAP's ScrollTrigger calls
// matchMedia internally. Report "no match" for everything: the motion hooks then treat the test DOM
// as reduced motion, so components render their static, fully visible state.
if (typeof window !== 'undefined' && typeof window.matchMedia !== 'function') {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }),
  })
}
