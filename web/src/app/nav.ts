import {
  ArrowLeftRight,
  Coins,
  FileText,
  FileUp,
  Gauge,
  Landmark,
  LayoutDashboard,
  Lightbulb,
  PiggyBank,
  Receipt,
  RefreshCw,
  Settings,
  TrendingUp,
  Wallet,
  type LucideIcon,
} from 'lucide-react'

export type PageId =
  | 'overview'
  | 'accounts'
  | 'sync-health'
  | 'transactions'
  | 'import'
  | 'budgets'
  | 'forecast'
  | 'investments'
  | 'crypto'
  | 'credit'
  | 'documents'
  | 'taxes'
  | 'advisor'
  | 'settings'

export interface PageDef {
  id: PageId
  path: string
  /** Desktop sidebar label. */
  label: string
  /** Mobile tab label, when it differs. */
  shortLabel?: string
  icon: LucideIcon
  /** PLAN Section 13 / 18.9 / 20.3 phase that delivers the page. */
  phase: string
  /** One line from PLAN Section 11. */
  summary: string
}

export const PAGES: Record<PageId, PageDef> = {
  overview: {
    id: 'overview',
    path: '/',
    label: 'Overview',
    shortLabel: 'Dashboard',
    icon: LayoutDashboard,
    phase: '5',
    summary: 'Net worth, cash flow, budget health, upcoming bills, forecast and findings.',
  },
  accounts: {
    id: 'accounts',
    path: '/accounts',
    label: 'Accounts',
    icon: Landmark,
    phase: '2',
    summary: 'Accounts grouped by type, balances, connection status and the Item slot counter.',
  },
  'sync-health': {
    id: 'sync-health',
    path: '/sync-health',
    label: 'Sync health',
    icon: RefreshCw,
    phase: '2',
    summary: 'Per-account status, last success, stale-data warnings and duplicate cleanup.',
  },
  transactions: {
    id: 'transactions',
    path: '/transactions',
    label: 'Transactions',
    icon: ArrowLeftRight,
    phase: '4',
    summary: 'Search, filters, bulk recategorize, splits, rules and receipts.',
  },
  import: {
    id: 'import',
    path: '/import',
    label: 'Import',
    icon: FileUp,
    phase: '3',
    summary: 'Upload a statement PDF, see it reconcile, and review what did not.',
  },
  budgets: {
    id: 'budgets',
    path: '/budgets',
    label: 'Budgets',
    icon: PiggyBank,
    phase: '5',
    summary: 'Pace bars, rollover, editable lines and history.',
  },
  forecast: {
    id: 'forecast',
    path: '/forecast',
    label: 'Forecast',
    icon: TrendingUp,
    phase: '5',
    summary: 'P10/P50/P90 balance bands, what-if scenarios and published backtest error.',
  },
  investments: {
    id: 'investments',
    path: '/investments',
    label: 'Investments',
    icon: Wallet,
    phase: '2',
    summary: 'Holdings, allocation, contributions against limits and retirement projection.',
  },
  crypto: {
    id: 'crypto',
    path: '/crypto',
    label: 'Crypto',
    icon: Coins,
    phase: '10a',
    summary: 'Watch-only wallets from xpubs and addresses, exchanges and price history.',
  },
  credit: {
    id: 'credit',
    path: '/credit',
    label: 'Credit',
    icon: Gauge,
    phase: '6',
    summary: 'Per-bureau reports, tradelines, utilization, inquiries and a score log.',
  },
  documents: {
    id: 'documents',
    path: '/documents',
    label: 'Documents',
    icon: FileText,
    phase: '3',
    summary: 'Upload statements and forms, parse status and field verification.',
  },
  taxes: {
    id: 'taxes',
    path: '/taxes',
    label: 'Taxes',
    icon: Receipt,
    phase: '7',
    summary: 'Optional tax-prep and planning module: live estimate, scenarios and checklist.',
  },
  advisor: {
    id: 'advisor',
    path: '/advisor',
    label: 'Advisor',
    icon: Lightbulb,
    phase: '8',
    summary: 'Private AI on the model you choose, with visible tool calls and a findings inbox.',
  },
  settings: {
    id: 'settings',
    path: '/settings',
    label: 'Settings',
    icon: Settings,
    phase: '1',
    summary: 'Household members, categories, rules, alerts, passkeys, backups and connections.',
  },
}

/** Desktop sidebar, in BRAND.md "App overview" order. */
export const SIDEBAR_PRIMARY: PageId[] = [
  'overview',
  'transactions',
  'import',
  'budgets',
  'forecast',
  'investments',
  'crypto',
  'advisor',
  'settings',
]

/** Remaining PLAN Section 11 pages, shown under the primary list on desktop. */
export const SIDEBAR_SECONDARY: PageId[] = [
  'accounts',
  'sync-health',
  'credit',
  'documents',
  'taxes',
]

/** Mobile bottom tab bar (PLAN 11.1); "More" opens a Sheet with MOBILE_MORE. */
export const MOBILE_TABS: PageId[] = ['overview', 'transactions', 'budgets', 'advisor']

export const MOBILE_MORE: PageId[] = [
  'import',
  'forecast',
  'investments',
  'crypto',
  'accounts',
  'sync-health',
  'credit',
  'documents',
  'taxes',
  'settings',
]
