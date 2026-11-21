import type { PlaidProductStatus, PlaidSetupStep } from '@/api/plaid'

/*
 * Copy for the Plaid setup checklist. The API sends step ids, states and reason codes; the words
 * live here. A reason this file doesn't know falls back to the step's generic line plus the code,
 * so a new server reason is still shown and never a blank.
 */

export const PLAID_LINKS = {
  signup: 'https://dashboard.plaid.com/signup',
  keys: 'https://dashboard.plaid.com/developers/keys',
  products: 'https://dashboard.plaid.com/settings/team/products',
  redirects: 'https://dashboard.plaid.com/developers/api',
  trial: 'https://dashboard.plaid.com/trial-plan',
} as const

export const STEP_TITLES: Record<PlaidSetupStep['id'], string> = {
  keys_present: 'Add your Plaid keys',
  keys_valid: 'Plaid accepts the keys',
  products: 'Products on your plan',
  redirect_uri: 'Redirect URI',
  item_slots: 'Bank login slots',
}

export const STATE_LABELS: Record<PlaidSetupStep['state'], string> = {
  done: 'Done',
  action: 'Needs you',
  blocked: 'Blocked',
  optional: 'Optional',
  unchecked: 'Not checked',
}

export const PRODUCT_LABELS: Record<string, string> = {
  transactions: 'Transactions',
  auth: 'Auth',
  balance: 'Balance',
  investments: 'Investments',
  liabilities: 'Liabilities',
  statements: 'Statements',
  identity: 'Identity',
  assets: 'Assets',
}

export const PRODUCT_STATE_LABELS: Record<PlaidProductStatus['state'], string> = {
  enabled: 'On',
  not_enabled: 'Not on your plan',
  implicit: 'Included',
  unchecked: 'Not checked',
  error: "Couldn't check",
}

export interface StepLink {
  href: string
  label: string
}

export interface StepCopy {
  body: string
  link?: StepLink
}

const ENV_FILE = 'Put it in .env on your server, then restart Holdfast.'

const REASONS: Record<string, StepCopy> = {
  client_id_missing: {
    body: `PLAID_CLIENT_ID is empty. Create a free Plaid account, copy the client ID and secret from its dashboard. ${ENV_FILE}`,
    link: { href: PLAID_LINKS.signup, label: 'Create a Plaid account' },
  },
  secret_sandbox_missing: {
    body: `PLAID_SECRET_SANDBOX is empty. Copy the Sandbox secret from the dashboard. ${ENV_FILE}`,
    link: { href: PLAID_LINKS.keys, label: 'Open your Plaid keys' },
  },
  secret_production_missing: {
    body: `PLAID_SECRET_PRODUCTION is empty. You get it once Plaid approves the free Trial plan. ${ENV_FILE}`,
    link: { href: PLAID_LINKS.trial, label: 'Apply for the Trial plan' },
  },
  INVALID_API_KEYS: {
    body: 'Plaid refused the keys. Check for a typo, and that the secret is for this environment: a Sandbox secret does not work in Production.',
    link: { href: PLAID_LINKS.keys, label: 'Open your Plaid keys' },
  },
  UNAUTHORIZED_ENVIRONMENT: {
    body: 'Your Plaid account is not approved for this environment yet. Stay in Sandbox until the Trial plan is approved.',
    link: { href: PLAID_LINKS.trial, label: 'Apply for the Trial plan' },
  },
  plaid_unreachable: {
    body: "Your server couldn't reach Plaid. Check that it has outbound internet access, then run the check again.",
  },
  no_products_configured: {
    body: 'PLAID_PRODUCTS lists nothing Holdfast can ask for. Add at least transactions.',
  },
  no_products_enabled: {
    body: 'None of the products in PLAID_PRODUCTS are on your Plaid plan. Turn them on in the dashboard.',
    link: { href: PLAID_LINKS.products, label: 'Open Plaid products' },
  },
  some_products_unavailable: {
    body: 'Some products are not on your plan. Holdfast skips those and syncs the rest.',
    link: { href: PLAID_LINKS.products, label: 'Open Plaid products' },
  },
  product_check_failed: {
    body: "Plaid didn't answer the product check. Run it again in a minute.",
  },
  not_set: {
    body: 'Not needed on a desktop browser. To link a bank from your phone, register the address below in Plaid and set PLAID_REDIRECT_URI to the same value.',
    link: { href: PLAID_LINKS.redirects, label: 'Open allowed redirect URIs' },
  },
  not_registered: {
    body: 'Plaid does not have this address on its allowlist. Add it exactly as shown, then run the check again.',
    link: { href: PLAID_LINKS.redirects, label: 'Open allowed redirect URIs' },
  },
  https_required: {
    body: 'Production needs an https:// address. Change PLAID_REDIRECT_URI and restart.',
  },
  http_only_on_localhost: {
    body: 'Plain http:// only works for localhost, and only in Sandbox. Use an https:// address.',
  },
  has_query_or_fragment: {
    body: 'Plaid does not allow a ? or # in the redirect URI. Remove it from PLAID_REDIRECT_URI.',
  },
  not_a_url: {
    body: 'PLAID_REDIRECT_URI is not a full address. It should start with https://.',
  },
  sandbox_is_unlimited: {
    body: 'Sandbox banks are fake and unlimited. Slots only count once you switch to real data.',
  },
  few_slots_left: {
    body: 'You are close to the Trial limit. Removing a bank never gives a slot back, so relink instead of re-adding.',
  },
  no_slots_left: {
    body: 'All Trial slots are used. Existing banks keep syncing and can be relinked; new ones cannot be added.',
  },
}

const DEFAULTS: Record<PlaidSetupStep['id'], Record<PlaidSetupStep['state'], string>> = {
  keys_present: {
    done: 'Client ID and secret are set for this environment.',
    action: 'A key is missing.',
    blocked: 'A key is missing.',
    optional: '',
    unchecked: '',
  },
  keys_valid: {
    done: 'Plaid answered with these keys.',
    action: 'Plaid refused the keys.',
    blocked: "Couldn't ask Plaid.",
    optional: '',
    unchecked: 'Run the check to try the keys against Plaid.',
  },
  products: {
    done: 'Every product you asked for is on your plan.',
    action: 'No product is available.',
    blocked: "Couldn't check products.",
    optional: '',
    unchecked: 'Checked once the keys work.',
  },
  redirect_uri: {
    done: 'Plaid has this address on its allowlist.',
    action: 'The redirect URI needs fixing.',
    blocked: "Couldn't check the redirect URI.",
    optional: 'Not needed on a desktop browser.',
    unchecked: 'Checked once the keys work.',
  },
  item_slots: {
    done: 'Each bank login uses one slot. Removing a bank never gives it back.',
    action: '',
    blocked: 'No slots left.',
    optional: '',
    unchecked: '',
  },
}

export function stepCopy(step: PlaidSetupStep): StepCopy {
  if (step.reason) {
    const known = REASONS[step.reason]
    if (known) return known
    return { body: `${DEFAULTS[step.id][step.state]} Plaid said: ${step.reason}.`.trim() }
  }
  return { body: DEFAULTS[step.id][step.state] }
}

export function productLabel(product: string): string {
  return PRODUCT_LABELS[product] ?? product
}
