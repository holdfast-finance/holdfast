/**
 * Minimal matchMedia for jsdom: evaluates min-width / max-width / prefers-color-scheme
 * against a fake viewport width and color scheme.
 */
export function mockViewport(width: number, scheme: 'dark' | 'light' = 'dark'): void {
  const evaluate = (query: string): boolean => {
    const min = /min-width:\s*(\d+)px/.exec(query)
    if (min?.[1]) return width >= Number.parseInt(min[1], 10)
    const max = /max-width:\s*(\d+)px/.exec(query)
    if (max?.[1]) return width <= Number.parseInt(max[1], 10)
    const color = /prefers-color-scheme:\s*(dark|light)/.exec(query)
    if (color?.[1]) return color[1] === scheme
    return false
  }

  Object.defineProperty(window, 'innerWidth', { configurable: true, value: width })
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: (query: string): MediaQueryList => ({
      matches: evaluate(query),
      media: query,
      onchange: null,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      // The MediaQueryList type still requires the legacy listener methods.
      /* eslint-disable @typescript-eslint/no-deprecated */
      addListener: () => undefined,
      removeListener: () => undefined,
      /* eslint-enable @typescript-eslint/no-deprecated */
      dispatchEvent: () => false,
    }),
  })
}
