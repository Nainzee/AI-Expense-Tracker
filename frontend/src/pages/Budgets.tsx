import { useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Save, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useCategories } from '../components/ReceiptEditor'
import { Badge, Button, Card, ErrorBox, inputCls, Spinner } from '../components/ui'
import { api, qs, type Budget, type Summary } from '../lib/api'
import { currentMonth, money, monthBounds } from '../lib/format'

export default function Budgets() {
  const qc = useQueryClient()
  const cats = useCategories().data?.categories ?? []
  const budgets = useQuery({ queryKey: ['budgets'], queryFn: () => api<Budget[]>('/budgets') })
  const month = currentMonth()
  const params = qs(monthBounds(month))
  const summary = useQuery({ queryKey: ['summary', params], queryFn: () => api<Summary>(`/analytics/summary${params}`) })
  const [limits, setLimits] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    if (budgets.data) setLimits(Object.fromEntries(budgets.data.map((b) => [b.category, String(b.monthly_limit)])))
  }, [budgets.data])

  const cur = summary.data?.currency ?? 'INR'
  const spent = Object.fromEntries((summary.data?.per_category ?? []).map((c) => [c.category, c.amount]))
  const status = Object.fromEntries((summary.data?.budget_status ?? []).map((b) => [b.category, b]))
  const alerts = (summary.data?.budget_status ?? []).filter((b) => b.status !== 'ok')

  const save = async () => {
    setSaving(true)
    setError(null)
    setSaved(false)
    try {
      const body = Object.entries(limits)
        .filter(([, v]) => v.trim() !== '' && Number(v) > 0)
        .map(([category, v]) => ({ category, monthly_limit: Number(v), currency: cur }))
      await api('/budgets', { method: 'PUT', body: JSON.stringify(body) })
      await qc.invalidateQueries()
      setSaved(true)
    } catch (err) {
      setError(err)
    } finally {
      setSaving(false)
    }
  }

  const remove = async (category: string) => {
    try {
      await api(`/budgets/${encodeURIComponent(category)}`, { method: 'DELETE' })
      await qc.invalidateQueries()
    } catch (err) {
      setError(err)
    }
  }

  if (budgets.isLoading) return <Spinner />

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold">Monthly budgets</h1>
        <Button onClick={save} loading={saving}>
          <Save className="h-4 w-4" aria-hidden="true" /> Save budgets
        </Button>
      </div>
      <ErrorBox error={error || budgets.error} />
      <p role="status" className="sr-only">{saved ? 'Budgets saved' : ''}</p>
      {alerts.length > 0 && (
        <div role="alert" className="space-y-1 rounded-lg border border-amber-400 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-200">
          {alerts.map((a) => (
            <p key={a.category} className="flex items-center gap-2">
              <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden="true" />
              {a.category}: {a.status === 'exceeded' ? `over budget by ${money(-a.remaining, cur)}` : `${a.percentage}% used, ${money(a.remaining, cur)} left`}
            </p>
          ))}
        </div>
      )}
      <Card title={`Limits for ${month} (${cur})`}>
        <ul className="divide-y divide-slate-100 dark:divide-slate-800">
          {cats.map((c) => {
            const st = status[c]
            const pct = st ? Math.min(st.percentage, 100) : 0
            return (
              <li key={c} className="grid grid-cols-12 items-center gap-3 py-3">
                <label htmlFor={`b-${c}`} className="col-span-12 text-sm font-medium sm:col-span-3">{c}</label>
                <div className="col-span-7 sm:col-span-3">
                  <input
                    id={`b-${c}`}
                    type="number"
                    min="0"
                    step="100"
                    inputMode="decimal"
                    placeholder="No limit"
                    className={inputCls}
                    value={limits[c] ?? ''}
                    onChange={(e) => setLimits({ ...limits, [c]: e.target.value })}
                  />
                </div>
                <div className="col-span-12 order-last sm:order-none sm:col-span-5">
                  <div className="mb-1 flex justify-between text-xs text-slate-600 dark:text-slate-400">
                    <span>Spent {money(spent[c] ?? 0, cur)}</span>
                    {st && <Badge tone={st.status === 'exceeded' ? 'red' : st.status === 'warning' ? 'amber' : 'green'}>{st.percentage}%</Badge>}
                  </div>
                  {st && (
                    <div className="h-2 rounded-full bg-slate-200 dark:bg-slate-800" role="progressbar" aria-label={`${c} budget used`} aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
                      <div className={`h-2 rounded-full ${st.status === 'exceeded' ? 'bg-red-500' : st.status === 'warning' ? 'bg-amber-500' : 'bg-emerald-500'}`} style={{ width: `${pct}%` }} />
                    </div>
                  )}
                </div>
                <div className="col-span-5 flex justify-end sm:col-span-1">
                  {budgets.data?.some((b) => b.category === c) && (
                    <button
                      type="button"
                      aria-label={`Remove ${c} budget`}
                      onClick={() => remove(c)}
                      className="inline-flex h-10 w-10 cursor-pointer items-center justify-center rounded-lg text-slate-600 hover:bg-red-50 hover:text-red-700 dark:text-slate-300 dark:hover:bg-red-950"
                    >
                      <Trash2 className="h-4 w-4" aria-hidden="true" />
                    </button>
                  )}
                </div>
              </li>
            )
          })}
        </ul>
      </Card>
    </div>
  )
}
