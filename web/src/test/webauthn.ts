import type { AuthenticationResponseJSON, RegistrationResponseJSON } from '@simplewebauthn/browser'

/*
 * Fake WebAuthn responses for tests. Each test file mocks '@simplewebauthn/browser':
 *
 *   vi.mock('@simplewebauthn/browser', () => ({ startRegistration: vi.fn(), startAuthentication: vi.fn() }))
 *
 * The MSW mock only checks the credential id, so the byte fields are placeholders.
 */

export function fakeRegistration(id: string): RegistrationResponseJSON {
  return {
    id,
    rawId: id,
    type: 'public-key',
    response: {
      clientDataJSON: 'e30',
      attestationObject: 'o2NmbXRkbm9uZQ',
      transports: ['internal'],
    },
    clientExtensionResults: {},
    authenticatorAttachment: 'platform',
  }
}

export function fakeAssertion(id: string): AuthenticationResponseJSON {
  return {
    id,
    rawId: id,
    type: 'public-key',
    response: {
      clientDataJSON: 'e30',
      authenticatorData: 'AAAA',
      signature: 'AAAA',
      userHandle: 'dXNlcg',
    },
    clientExtensionResults: {},
  }
}

/** What the browser throws when the user closes the passkey prompt. */
export function notAllowed(): Error {
  const error = new Error('The operation either timed out or was not allowed.')
  error.name = 'NotAllowedError'
  return error
}
