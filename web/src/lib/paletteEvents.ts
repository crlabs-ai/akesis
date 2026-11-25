/** Opens the command palette from anywhere (the header button uses this). */
export const PALETTE_OPEN_EVENT = 'akesis:open-palette'

export const openCommandPalette = (): void => {
  window.dispatchEvent(new CustomEvent(PALETTE_OPEN_EVENT))
}
