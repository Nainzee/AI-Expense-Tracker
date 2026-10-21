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

export async function request(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers)
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  if (init.body && !(init.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  const res = await fetch(`/api/v1${path}`, { ...init, headers })
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
