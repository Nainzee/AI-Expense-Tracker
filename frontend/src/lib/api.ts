// Typed API client. Token lives in localStorage; a 401 clears it and sends the user to /login.

const TOKEN_KEY = 'auth_token'

export const getToken = () => localStorage.getItem(TOKEN_KEY)
export const setToken = (t: string) => localStorage.setItem(TOKEN_KEY, t)
export const clearToken = () => localStorage.removeItem(TOKEN_KEY)

export class ApiError extends Error {
  status: number
  detail: unknown
  constructor(status: number, message: string, detail?: unknown) {
    super(message)
    this.status = status
    this.detail = detail
  }
}

function messageFrom(detail: unknown, fallback: string): string {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) return detail.map((d) => (d as { msg?: string }).msg ?? '').filter(Boolean).join('; ') || fallback
  if (detail && typeof detail === 'object' && 'message' in detail) return String((detail as { message: unknown }).message)
  return fallback
}

const API_BASE = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '')

export async function request(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers)
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  if (init.body && !(init.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  const res = await fetch(`${API_BASE}/api/v1${path}`, { ...init, headers })
  if (res.status === 401 && !path.startsWith('/auth/login')) {
    clearToken()
    if (window.location.pathname !== '/login') window.location.assign('/login')
  }
  if (!res.ok) {
    let detail: unknown
    try {
      detail = (await res.json()).detail
    } catch {
      detail = undefined
    }
    throw new ApiError(res.status, messageFrom(detail, `Request failed (${res.status})`), detail)
  }
  return res
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await request(path, init)
  return (await res.json()) as T
}

export function qs(params: Record<string, string | undefined | null>): string {
  const p = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v) p.set(k, v)
  const s = p.toString()
  return s ? `?${s}` : ''
}

// ─── Types ──────────────────────────────────────────────────────────────────
export interface Item {
  item_name: string
  amount: number
  category: string
  quantity?: number | null
  unit_price?: number | null
  discount?: number | null
  category_confidence?: number
  category_source?: string
  low_confidence?: boolean
  original_category?: string | null
}

export interface Extraction {
  merchant_name: string
  merchant_address: string | null
  date: string | null
  time: string | null
  invoice_number: string | null
  currency: string
  subtotal: number | null
  tax: number | null
  total: number | null
  payment_method: string | null
}

export interface Reconciliation {
  status: 'match' | 'mismatch' | 'unverified'
  items_sum: number
  reference: string | null
  difference: number | null
}

export interface ScanResult {
  extraction: Extraction
  items: Item[]
  low_confidence_fields: string[]
  engine_used: string
  fallback_reason: string | null
  reconciliation: Reconciliation
  duplicate_of: string | null
  upload_id: string
  raw_text: string
}

export interface ReceiptSummary {
  id: string
  date: string
  store: string
  total: number
  currency: string
  engine_used: string | null
  reconciliation_status: string | null
  has_file: boolean
  item_count: number
  categories: string[]
  split?: { you_lent: number; you_owe: number; payer: string } | null
  linked_transaction_id?: number | null
}

export interface Receipt {
  id: string
  date: string
  store: string
  subtotal: number
  tax: number
  total: number
  currency: string
  payment_method: string
  notes: string
  merchant_address: string | null
  time: string | null
  invoice_number: string | null
  engine_used: string | null
  reconciliation_status: string | null
  raw_text: string | null
  has_file: boolean
  items: (Item & { id: number })[]
  split?: Split | null
  bank_match?: BankMatch
}

export interface ReceiptPayload extends Omit<Extraction, 'merchant_name'> {
  merchant_name: string
  notes?: string
  items: Item[]
  upload_id?: string | null
  engine_used?: string | null
  reconciliation_status?: string | null
  raw_text?: string | null
  force?: boolean
}

export interface BudgetStatus {
  category: string
  limit: number
  spent: number
  remaining: number
  percentage: number
  status: 'ok' | 'warning' | 'exceeded'
  month: string
  currency: string
}

export interface Summary {
  currency: string
  currencies: string[]
  total: number
  receipt_count: number
  average_per_receipt: number
  per_category: { category: string; amount: number; count: number }[]
  per_merchant: { merchant: string; amount: number; count: number }[]
  top_items: { item_name: string; category: string; amount: number; store: string; date: string }[]
  monthly_trend: { month: string; amount: number; count: number }[]
  month_over_month: { month: string; previous_month: string; current: number; previous: number; change_pct: number | null }
  forecast: { month: string; spent: number; day: number; days_in_month: number; daily_average: number; projected_total: number }
  budget_status: BudgetStatus[]
  split_count: number
  transaction_count: number
  payment_count: number
  source_mix: SourceMix
}

export interface SourceMix {
  receipts: { amount: number; count: number }
  statements: { amount: number; count: number }
  linked_count: number
  needs_review_count: number
}

export interface Budget {
  category: string
  monthly_limit: number
  currency: string
}

export interface Insights {
  month: string
  currency: string
  summary: string
  tips: string[]
  anomalies: string[]
  source: 'ai' | 'rules'
}

export interface ChatReply {
  answer: string
  tool_calls: { name: string; arguments: Record<string, unknown> }[]
  source: 'ai' | 'rules'
}

export interface Health {
  version: string
  auth_enabled: boolean
  ollama: { available: boolean; models: string[]; vlm: string; text_model: string }
}

// ─── Bill split ─────────────────────────────────────────────────────────────
export interface Person {
  id: number
  name: string
}

export type SplitMethod = 'equal' | 'exact' | 'percent' | 'shares' | 'items'

/** person_id null means "You". */
export interface SplitIn {
  payer_id: number | null
  method: SplitMethod
  participants: { person_id: number | null; value?: number | null }[]
  items: { item_index: number; person_ids: (number | null)[] }[]
}

export interface Split {
  receipt_id: string
  method: SplitMethod
  currency: string
  total: number
  payer: { id: number | null; name: string }
  your_share: number
  you_lent: number
  you_owe: number
  shares: { person_id: number | null; name: string; amount: number; value: number | null }[]
  items: { item_index: number; person_ids: (number | null)[] }[]
  created_at: string
  updated_at: string
}

export interface SplitSummary {
  receipt_id: string
  store: string
  date: string
  method: SplitMethod
  currency: string
  total: number
  payer: string
  your_share: number
  you_lent: number
  you_owe: number
}

/** net > 0 means the person owes you. */
export interface Balances {
  totals: { currency: string; owed_to_you: number; you_owe: number }[]
  people: { person_id: number; name: string; currency: string; net: number }[]
}

// ─── Bank / UPI statements ──────────────────────────────────────────────────
export type TxnType =
  | 'expense'
  | 'income'
  | 'transfer_self'
  | 'transfer_person'
  | 'refund'
  | 'card_bill_payment'
  | 'cash_withdrawal'
  | 'investment'

export const TXN_TYPE_LABEL: Record<TxnType, string> = {
  expense: 'Spending',
  transfer_person: 'Paid a person',
  cash_withdrawal: 'Cash withdrawal',
  income: 'Income',
  refund: 'Refund',
  transfer_self: 'Own-account transfer',
  card_bill_payment: 'Credit card bill',
  investment: 'Investment',
}

export interface ColumnMapping {
  header_row: number
  date: number
  description: number
  debit?: number | null
  credit?: number | null
  amount?: number | null
  drcr?: number | null
  balance?: number | null
  reference?: number | null
  date_order: 'dmy' | 'mdy' | 'ymd'
}

export interface PreviewTxn {
  date: string
  description: string
  amount: number
  direction: 'debit' | 'credit'
  balance: number | null
  merchant: string | null
  txn_type: TxnType
  category: string
  needs_review: number
}

export interface StatementPreview {
  token: string
  filename: string
  format: 'csv' | 'xlsx' | 'xls' | 'pdf'
  source: string
  account_hint: string | null
  period_start: string | null
  period_end: string | null
  columns: string[]
  sample_rows: string[][]
  mapping: ColumnMapping | null
  needs_mapping: boolean
  rows_parsed: number
  rows_skipped: number
  likely_duplicates: number
  likely_new: number
  already_imported: boolean
  transactions: PreviewTxn[]
}

export interface ImportSummary {
  statement_id: number | null
  rows_parsed: number
  new: number
  duplicates_skipped: number
  auto_categorized: number
  needs_review: number
  matched_receipts: number
  suggested_matches: number
  rows_skipped: number
}

export interface Statement {
  id: number
  filename: string
  format: string
  source: string
  account_label: string
  account_hint: string | null
  period_start: string | null
  period_end: string | null
  rows_new: number
  rows_duplicate: number
  imported_at: string
  txn_count: number
}

export interface TxnBrief {
  id: number
  date: string
  description: string
  amount: number
  direction: 'debit' | 'credit'
  currency: string
  merchant: string | null
  category: string
  txn_type: TxnType
  match_source: 'auto' | 'user' | null
  match_score: number | null
}

export interface Transaction extends TxnBrief {
  balance: number | null
  reference: string | null
  counterparty: string | null
  vpa: string | null
  payee_key: string | null
  channel: string | null
  category_source: 'rule' | 'memory' | 'ai' | 'user' | 'none'
  confidence: number
  needs_review: boolean
  excluded: boolean
  matched_receipt_id: string | null
  counted: boolean
  receipt: { id: string; store: string; date: string; total: number } | null
  suggestion_count: number
}

export interface MatchSuggestion {
  transaction: TxnBrief
  receipt: { id: string; store: string; date: string; total: number; currency: string }
  score: number
}

export interface ReviewQueue {
  needs_review: Transaction[]
  match_suggestions: MatchSuggestion[]
  count: number
}

export interface RecurringPayment {
  payee_key: string
  merchant: string
  category: string | null
  cadence: 'monthly' | 'weekly'
  count: number
  average_amount: number
  last_date: string
  next_expected: string
  currency: string
  monthly_cost: number
}

export interface BankMatch {
  linked: TxnBrief | null
  suggestions: { transaction: TxnBrief; score: number }[]
}
