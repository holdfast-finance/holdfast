import type { PlaidProductStatus, PlaidSetup, PlaidSetupStep } from '@/api/plaid'

/*
 * Plaid setup scenario for the mock API. Tests set fields on `db.plaid` to reach each state; the
 * functions below answer the way the real checklist does (backend `app/domain/plaid_preflight.py`).
 * Nothing here is a real key, token or request id.
 */

export interface PlaidScenario {
  env: 'sandbox' | 'production'
  hasClientId: boolean
  hasSecret: boolean
  /** What Plaid answers when the keys are tried. */
  keys: 'ok' | 'rejected' | 'unreachable'
  products: string[]
  /** Configured products the plan does not include. */
  notEnabled: string[]
  redirectUri: string | null
  redirectRegistered: boolean
  slotsUsed: number
  /** Live checks answered so far; the 7th in a row answers 429 like the real limit. */
  checks: number
}

export const MOCK_REQUEST_ID = 'mockreq0001'
export const MOCK_SUGGESTED_REDIRECT = 'https://localhost/plaid/oauth-return'
export const MOCK_CHECK_LIMIT = 6

export function defaultPlaidScenario(): PlaidScenario {
  return {
    env: 'sandbox',
    hasClientId: true,
    hasSecret: true,
    keys: 'ok',
    products: ['transactions', 'balance', 'investments', 'liabilities'],
    notEnabled: [],
    redirectUri: null,
    redirectRegistered: false,
    slotsUsed: 0,
    checks: 0,
  }
}

function step(
  id: PlaidSetupStep['id'],
  state: PlaidSetupStep['state'],
  reason: string | null = null,
  requestId: string | null = null,
): PlaidSetupStep {
  return { id, state, reason, request_id: requestId }
}

/** The checklist for a scenario. `live` is false for GET (config only), true for a check. */
export function plaidSetup(s: PlaidScenario, live: boolean): PlaidSetup {
  const configured = s.hasClientId && s.hasSecret
  const probed = live && configured
  const keysOk = probed && s.keys === 'ok'

  const present = !s.hasClientId
    ? step('keys_present', 'action', 'client_id_missing')
    : !s.hasSecret
      ? step('keys_present', 'action', `secret_${s.env}_missing`)
      : step('keys_present', 'done')

  const valid = !probed
    ? step('keys_valid', 'unchecked')
    : s.keys === 'ok'
      ? step('keys_valid', 'done')
      : s.keys === 'unreachable'
        ? step('keys_valid', 'blocked', 'plaid_unreachable')
        : step('keys_valid', 'action', 'INVALID_API_KEYS', MOCK_REQUEST_ID)

  const products: PlaidProductStatus[] = s.products.map((product) => {
    if (product === 'balance') return { product, state: 'implicit', reason: null }
    if (!keysOk) return { product, state: 'unchecked', reason: null }
    return s.notEnabled.includes(product)
      ? { product, state: 'not_enabled', reason: 'INVALID_PRODUCT' }
      : { product, state: 'enabled', reason: null }
  })
  const asked = products.filter((p) => p.state !== 'implicit')
  const anyEnabled = asked.some((p) => p.state === 'enabled')
  const productStep = !keysOk
    ? step('products', 'unchecked')
    : anyEnabled
      ? step(
          'products',
          'done',
          asked.some((p) => p.state === 'not_enabled') ? 'some_products_unavailable' : null,
        )
      : step('products', 'action', 'no_products_enabled')

  let redirect: PlaidSetupStep
  if (configured && probed && !keysOk) redirect = step('redirect_uri', 'unchecked')
  else if (!configured) redirect = step('redirect_uri', 'unchecked')
  else if (s.redirectUri === null) redirect = step('redirect_uri', 'optional', 'not_set')
  else if (s.env === 'production' && s.redirectUri.startsWith('http://'))
    redirect = step('redirect_uri', 'action', 'https_required')
  else if (!keysOk) redirect = step('redirect_uri', 'unchecked')
  else
    redirect = s.redirectRegistered
      ? step('redirect_uri', 'done')
      : step('redirect_uri', 'action', 'not_registered', MOCK_REQUEST_ID)

  const slots =
    s.env === 'sandbox'
      ? step('item_slots', 'optional', 'sandbox_is_unlimited')
      : s.slotsUsed >= 10
        ? step('item_slots', 'blocked', 'no_slots_left')
        : step('item_slots', 'done', s.slotsUsed >= 8 ? 'few_slots_left' : null)

  const ready =
    present.state === 'done' &&
    valid.state === 'done' &&
    productStep.state === 'done' &&
    (redirect.state === 'done' || redirect.state === 'optional') &&
    slots.state !== 'blocked'
  return {
    env: s.env,
    ready,
    checked: probed,
    steps: [present, valid, productStep, redirect, slots],
    products,
    redirect_uri: s.redirectUri,
    suggested_redirect_uri: MOCK_SUGGESTED_REDIRECT,
    item_slots_used: s.env === 'sandbox' ? 0 : s.slotsUsed,
    item_slots_total: 10,
    ledger_orphans: 0,
    tokens_unreadable: 0,
    warnings: [],
  }
}
