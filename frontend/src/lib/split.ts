// Client-side mirror of backend/splits.py money math, used only for the live preview.
// The server recomputes and validates every split.
import type { SplitMethod } from './api'

const EXP0 = new Set(['JPY', 'KRW', 'VND', 'CLP', 'ISK', 'UGX', 'PYG'])
const EXP3 = new Set(['BHD', 'KWD', 'OMR', 'JOD', 'TND'])

export const minorExp = (cur: string) => (EXP0.has(cur) ? 0 : EXP3.has(cur) ? 3 : 2)
// Round half away from zero, like the backend's Decimal ROUND_HALF_UP (toFixed absorbs float noise such as 1.005 * 100)
export const toMinor = (amount: number, cur: string) => Math.sign(amount) * Math.round(Number((Math.abs(amount) * 10 ** minorExp(cur)).toFixed(6)))
export const fromMinor = (n: number, cur: string) => n / 10 ** minorExp(cur)

/** Largest-remainder allocation; the result always sums to total. */
export function allocate(total: number, weights: number[]): number[] {
  if (total < 0) return allocate(-total, weights).map((x) => -x)
  const sum = weights.reduce((s, w) => s + w, 0)
  if (!weights.length || sum <= 0) throw new Error('Split weights must add up to more than zero.')
  const raw = weights.map((w) => (total * w) / sum)
  const out = raw.map((r) => Math.floor(r))
  let left = total - out.reduce((s, x) => s + x, 0)
  const order = raw.map((r, i) => [r - out[i], i]).sort((a, b) => b[0] - a[0] || a[1] - b[1])
  for (const [, i] of order) {
    if (left <= 0) break
    out[i] += 1
    left -= 1
  }
  return out
}

export interface Preview {
  /** Minor units per row, or null when the input isn't valid yet. */
  amounts: number[] | null
  /** exact: minor units left to assign; percent: percentage points left. */
  remaining: number | null
  error: string | null
}

/**
 * values: per-row input: minor units for exact, numbers for percent/shares (ignored for equal/items).
 * items: per line item, its amount (minor) and the row indexes it's assigned to.
 */
export function computeShares(method: SplitMethod, totalMinor: number, values: (number | null)[], items: { minor: number; rows: number[] }[]): Preview {
  const n = values.length
  try {
    if (n === 0) return { amounts: null, remaining: null, error: 'Add at least one person.' }
    if (method === 'equal') return { amounts: allocate(totalMinor, values.map(() => 1)), remaining: null, error: null }
    if (method === 'exact') {
      const v = values.map((x) => (x === null || x < 0 ? null : x))
      const amounts = v.map((x) => (x === null ? 0 : x))
      const remaining = totalMinor - amounts.reduce((s, x) => s + x, 0)
      if (v.some((x) => x === null)) return { amounts: null, remaining, error: 'Enter an amount for everyone.' }
      return { amounts: remaining === 0 ? amounts : null, remaining, error: remaining === 0 ? null : 'Amounts must add up to the total.' }
    }
    if (method === 'percent') {
      const sum = values.reduce<number>((s, x) => s + (x ?? 0), 0)
      const remaining = Math.round((100 - sum) * 100) / 100
      if (values.some((x) => x === null || x < 0)) return { amounts: null, remaining, error: 'Enter a percentage for everyone.' }
      if (remaining !== 0) return { amounts: null, remaining, error: 'Percentages must add up to 100.' }
      return { amounts: allocate(totalMinor, values as number[]), remaining, error: null }
    }
    if (method === 'shares') {
      if (values.some((x) => x === null || x <= 0)) return { amounts: null, remaining: null, error: 'Shares must be greater than zero.' }
      return { amounts: allocate(totalMinor, values as number[]), remaining: null, error: null }
    }
    // items
    if (!items.length) return { amounts: null, remaining: null, error: 'This receipt has no line items.' }
    const sub = values.map(() => 0)
    for (const it of items) {
      if (!it.rows.length) return { amounts: null, remaining: null, error: 'Assign every item to at least one person.' }
      allocate(it.minor, it.rows.map(() => 1)).forEach((part, k) => (sub[it.rows[k]] += part))
    }
    if (sub.some((x) => x < 0) || sub.reduce((s, x) => s + x, 0) <= 0) return { amounts: null, remaining: null, error: 'Item amounts must add up to more than zero.' }
    return { amounts: allocate(totalMinor, sub), remaining: null, error: null }
  } catch (e) {
    return { amounts: null, remaining: null, error: e instanceof Error ? e.message : String(e) }
  }
}
