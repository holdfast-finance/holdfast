import { errorMessage, isApiError } from '@/api/client'
import type { LoginKind, SlotOut } from '@/api/connections'
import { ERROR_CODES } from '@/api/contracts'

export const LOGIN_KINDS: readonly { id: LoginKind; label: string; hint: string }[] = [
  { id: 'bank_card', label: 'Bank or card', hint: 'Checking, savings, credit cards, loans.' },
  { id: 'retirement_hsa', label: '401(k) or HSA', hint: 'Retirement and health savings logins.' },
  { id: 'brokerage', label: 'Brokerage', hint: 'Investment accounts.' },
]

export const PRODUCT_LABELS: Record<string, string> = {
  transactions: 'Transactions',
  liabilities: 'Liabilities (loan and card terms)',
  investments: 'Investments',
}

export const STATUS_LABELS: Record<string, string> = {
  pending_setup: 'Accounts to map',
  active: 'Synced',
  relink_needed: 'Relink needed',
  error: 'Error',
}

export const SLOT_STATE_LABELS: Record<SlotOut['state'], string> = {
  reserved: 'Link open',
  linked: 'Linked',
  abandoned: 'Released',
  unknown: 'Unconfirmed',
}

export const COUNT_SOURCE_LABELS: Record<string, string> = {
  owner_slot_log: 'your own slot log, from your Plaid dashboard notes',
}

/** Link-token failures, in plain words. Plaid's own message text is never shown. */
export function linkErrorMessage(error: unknown): string {
  if (isApiError(error)) {
    switch (error.code) {
      case ERROR_CODES.noSlotsLeft:
        return 'All 10 Plaid slots are used. Holdfast never asks for an 11th.'
      case ERROR_CODES.confirmRequired:
        return 'The slot phrase or your count did not match. Check them and try again.'
      case ERROR_CODES.productNotAllowed:
        return 'PLAID_PRODUCTS holds a product Holdfast does not request. Allowed: transactions, liabilities, investments. Change it in .env and restart.'
      case ERROR_CODES.daysNotAllowed:
        return 'Holdfast asks Plaid for 730 days of history and no other number. Fix PLAID_TX_DAYS_REQUESTED in .env.'
      case ERROR_CODES.sandboxOnRealData:
        return 'Sandbox links are off while this household holds real data.'
      case ERROR_CODES.sandboxConnectionPresent:
        return 'Remove the Sandbox connection before linking a real bank.'
      case ERROR_CODES.busy:
        return 'Another link is still open. Finish or release it first.'
      case ERROR_CODES.plaidUnavailable:
        return "Plaid couldn't be reached. Nothing changed. Try again in a minute."
      case ERROR_CODES.plaidError:
        return 'Plaid refused the request. Nothing was linked.'
    }
  }
  return errorMessage(error)
}

/** Abandon failures. 502 is retryable and keeps the slot held; 409 means Link may have worked. */
export function abandonErrorMessage(error: unknown): string {
  if (isApiError(error)) {
    if (error.code === ERROR_CODES.plaidUnavailable) {
      return "Plaid couldn't confirm that no bank was linked, so Holdfast kept the slot held. Nothing is lost. Try again in a minute."
    }
    if (error.code === ERROR_CODES.conflict) {
      return 'Plaid says Link made a connection, or this link is already settled. Check with Plaid instead of releasing.'
    }
    if (error.code === ERROR_CODES.confirmRequired) {
      return 'That is not the phrase. Type it exactly as shown.'
    }
    if (error.code === ERROR_CODES.notFound) return "That link doesn't exist any more."
  }
  return errorMessage(error)
}

export function isAbandonConflict(error: unknown): boolean {
  return isApiError(error, ERROR_CODES.conflict)
}
