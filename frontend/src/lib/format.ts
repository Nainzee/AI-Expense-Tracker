const formatters = new Map<string, Intl.NumberFormat>()

export function money(amount: number | null | undefined, currency = 'INR'): string {
  if (amount === null || amount === undefined || Number.isNaN(amount)) return '—'
  let f = formatters.get(currency)
  if (!f) {
    try {
      f = new Intl.NumberFormat('en-IN', { style: 'currency', currency })
    } catch {
      f = new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR' })
    }
    formatters.set(currency, f)
  }
  return f.format(amount)
}

export const CATEGORY_COLORS: Record<string, string> = {
  Food: '#10B981',
  Drinks: '#3B82F6',
  Dining: '#F97316',
  Bakery: '#EC4899',
  Household: '#F59E0B',
  'Personal Care': '#8B5CF6',
  Medical: '#EF4444',
  Transportation: '#14B8A6',
  Electronics: '#06B6D4',
  Apparel: '#6366F1',
  'Home & Hardware': '#78716C',
  Entertainment: '#A855F7',
  Utilities: '#EAB308',
  'Office & Stationery': '#0284C7',
  Other: '#64748B',
}

export const categoryColor = (c: string) => CATEGORY_COLORS[c] ?? '#64748B'

const pad = (n: number) => String(n).padStart(2, '0')
export const isoDate = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
export const currentMonth = () => isoDate(new Date()).slice(0, 7)

export function monthBounds(month: string): { from: string; to: string } {
  const [y, m] = month.split('-').map(Number)
  const last = new Date(y, m, 0).getDate()
  return { from: `${month}-01`, to: `${month}-${pad(last)}` }
}
