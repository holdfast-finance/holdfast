import '@testing-library/jest-dom/vitest'

import { cleanup } from '@testing-library/react'
import { afterEach, vi } from 'vitest'

import { mockViewport } from './matchMedia'

// Default to a phone-sized viewport (mobile-first); tests opt into desktop explicitly.
mockViewport(375)

// Radix measures elements with ResizeObserver, which jsdom lacks.
class ResizeObserverStub {
  observe(): void {
    // no-op
  }
  unobserve(): void {
    // no-op
  }
  disconnect(): void {
    // no-op
  }
}
vi.stubGlobal('ResizeObserver', ResizeObserverStub)

// The app makes no network requests in tests unless a test stubs fetch itself.
vi.stubGlobal(
  'fetch',
  vi.fn(() => Promise.reject(new Error('Network is disabled in tests'))),
)

afterEach(() => {
  cleanup()
  window.localStorage.clear()
  mockViewport(375)
})
