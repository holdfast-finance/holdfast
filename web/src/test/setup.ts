import '@testing-library/jest-dom/vitest'

import { cleanup } from '@testing-library/react'
import { afterAll, afterEach, beforeAll, vi } from 'vitest'

import { resetDb } from '@/mocks/db'
import { server } from '@/mocks/node'
import { useReauthStore } from '@/stores/reauth'

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

// jsdom has no WebAuthn and isn't a secure context. Tests mock @simplewebauthn/browser, so
// pretend to be a secure browser that has it; the HTTPS-notice tests override these.
vi.stubGlobal('isSecureContext', true)
vi.stubGlobal('PublicKeyCredential', function PublicKeyCredential() {
  // never constructed
})

// The only "network" in tests is the MSW mock of the API contract. Anything it doesn't handle
// fails the test, so no request can leave the process.
beforeAll(() => {
  server.listen({ onUnhandledRequest: 'error' })
})

afterEach(() => {
  cleanup()
  server.resetHandlers()
  resetDb()
  useReauthStore.setState({ pending: null })
  window.localStorage.clear()
  window.sessionStorage.clear()
  mockViewport(375)
})

afterAll(() => {
  server.close()
})
