import { useQuery } from '@tanstack/react-query'
import { TrendingDown, TrendingUp } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { Badge, Card, Empty, ErrorBox, inputCls, Spinner } from '../components/ui'
import { api, qs, type Summary } from '../lib/api'
import { categoryColor, isoDate, money } from '../lib/format'

type Preset = 'month' | '3m' | 'year' | 'all' | 'custom'

function rangeFor(p: Preset): { from: string; to: string } {
  const now = new Date()
  if (p === 'month') return { from: isoDate(new Date(now.getFullYear(), now.getMonth(), 1)), to: isoDate(now) }
  if (p === '3m') return { from: isoDate(new Date(now.getFullYear(), now.getMonth() - 2, 1)), to: isoDate(now) }
  if (p === 'year') return { from: `${now.getFullYear()}-01-01`, to: isoDate(now) }
  return { from: '', to: '' }
}

function Kpi({ label, value, sub }: { label: string; value: string; sub?: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <p className="text-sm text-slate-600 dark:text-slate-400">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums">{value}</p>
      {sub && <div className="mt-1 text-xs text-slate-600 dark:text-slate-400">{sub}</div>}
    </div>
  )
}

const tooltipStyle = { backgroundColor: 'var(--tt-bg, #fff)', borderRadius: 8, border: '1px solid #cbd5e1', color: '#0f172a' }

export default function Dashboard() {
  const [preset, setPreset] = useState<Preset>('all')
  const [custom, setCustom] = useState({ from: '', to: '' })
  const [currency, setCurrency] = useState('')
  const range = preset === 'custom' ? custom : rangeFor(preset)
  const params = qs({ ...range, currency })
  const q = useQuery({ queryKey: ['summary', params], queryFn: () => api<Summary>(`/analytics/summary${params}`) })
  const s = q.data
  const cur = s?.currency ?? 'INR'
  const fmt = (v: unknown) => money(Number(v), cur)

  const donut = useMemo(() => s?.per_category.filter((c) => c.amount > 0) ?? [], [s])
  const mom = s?.month_over_month

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Dashboard</h1>
          {s && s.split_count > 0 && <p className="text-sm text-slate-600 dark:text-slate-400">Split bills count only your share.</p>}
          {s && s.source_mix.statements.count + s.source_mix.linked_count > 0 && (
            <p className="text-sm text-slate-600 dark:text-slate-400">
              {money(s.source_mix.receipts.amount, cur)} from receipts · {money(s.source_mix.statements.amount, cur)} from bank/UPI
              {s.source_mix.linked_count > 0 && ` · ${s.source_mix.linked_count} payment${s.source_mix.linked_count === 1 ? '' : 's'} matched to receipts, counted once`}
              {s.source_mix.needs_review_count > 0 && (
                <>
                  {' · '}
                  <Link to="/statements/review" className="font-medium text-indigo-700 underline dark:text-indigo-300">{s.source_mix.needs_review_count} to review</Link>
                </>
              )}
            </p>
          )}
        </div>
        <div className="flex flex-wrap items-end gap-2">
          <div className="flex flex-col gap-1">
            <label htmlFor="d-range" className="text-xs font-medium text-slate-700 dark:text-slate-300">Period</label>
            <select id="d-range" className={inputCls} value={preset} onChange={(e) => setPreset(e.target.value as Preset)}>
              <option value="month">This month</option>
              <option value="3m">Last 3 months</option>
              <option value="year">This year</option>
              <option value="all">All time</option>
              <option value="custom">Custom range</option>
            </select>
          </div>
          {preset === 'custom' && (
            <>
              <div className="flex flex-col gap-1">
                <label htmlFor="d-from" className="text-xs font-medium text-slate-700 dark:text-slate-300">From</label>
                <input id="d-from" type="date" className={inputCls} value={custom.from} onChange={(e) => setCustom({ ...custom, from: e.target.value })} />
              </div>
              <div className="flex flex-col gap-1">
                <label htmlFor="d-to" className="text-xs font-medium text-slate-700 dark:text-slate-300">To</label>
                <input id="d-to" type="date" className={inputCls} value={custom.to} onChange={(e) => setCustom({ ...custom, to: e.target.value })} />
              </div>
            </>
          )}
          {s && s.currencies.length > 1 && (
            <div className="flex flex-col gap-1">
              <label htmlFor="d-cur" className="text-xs font-medium text-slate-700 dark:text-slate-300">Currency</label>
              <select id="d-cur" className={inputCls} value={currency || s.currency} onChange={(e) => setCurrency(e.target.value)}>
                {s.currencies.map((c) => (
                  <option key={c}>{c}</option>
                ))}
              </select>
            </div>
          )}
        </div>
      </div>

      {q.isLoading && <Spinner />}
      <ErrorBox error={q.error} />
      {s && s.payment_count === 0 && (
        <Card>
          <Empty>
            No spending in this period yet.{' '}
            <Link to="/upload" className="font-medium text-indigo-700 underline dark:text-indigo-300">Scan a receipt</Link> or{' '}
            <Link to="/statements" className="font-medium text-indigo-700 underline dark:text-indigo-300">import a bank statement</Link>.
          </Empty>
        </Card>
      )}
      {s && s.payment_count > 0 && (
        <>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Kpi
              label="Total spent"
              value={money(s.total, cur)}
              sub={`${s.receipt_count} receipt${s.receipt_count === 1 ? '' : 's'}${s.transaction_count ? ` · ${s.transaction_count} bank payment${s.transaction_count === 1 ? '' : 's'}` : ''}`}
            />
            <Kpi label="Average per payment" value={money(s.average_per_receipt, cur)} />
            <Kpi
              label={`This month (${mom?.month})`}
              value={money(mom?.current, cur)}
              sub={
                mom?.change_pct !== null && mom?.change_pct !== undefined ? (
                  <span className="inline-flex items-center gap-1">
                    {mom.change_pct > 0 ? <TrendingUp className="h-3.5 w-3.5" aria-hidden="true" /> : <TrendingDown className="h-3.5 w-3.5" aria-hidden="true" />}
                    {mom.change_pct > 0 ? '+' : ''}
                    {mom.change_pct}% vs {mom.previous_month}
                  </span>
                ) : (
                  `No data for ${mom?.previous_month}`
                )
              }
            />
            <Kpi
              label="Month-end forecast"
              value={money(s.forecast.projected_total, cur)}
              sub={`${money(s.forecast.daily_average, cur)}/day · day ${s.forecast.day} of ${s.forecast.days_in_month}`}
            />
          </div>

          <div className="grid gap-6 lg:grid-cols-2">
            <Card title="Spending by category">
              <div className="h-72" role="img" aria-label={`Category breakdown: ${donut.map((c) => `${c.category} ${fmt(c.amount)}`).join(', ')}`}>
                <ResponsiveContainer>
                  <PieChart>
                    <Pie data={donut} dataKey="amount" nameKey="category" innerRadius="55%" outerRadius="85%" paddingAngle={2}>
                      {donut.map((c) => (
                        <Cell key={c.category} fill={categoryColor(c.category)} />
                      ))}
                    </Pie>
                    <Tooltip formatter={fmt} contentStyle={tooltipStyle} />
                    <Legend />
                  </PieChart>
                </ResponsiveContainer>
              </div>
            </Card>
            <Card title="Monthly trend">
              <div className="h-72" role="img" aria-label={`Monthly totals: ${s.monthly_trend.map((m) => `${m.month} ${fmt(m.amount)}`).join(', ')}`}>
                <ResponsiveContainer>
                  <LineChart data={s.monthly_trend} margin={{ left: 8, right: 16, top: 8 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#94a3b8" strokeOpacity={0.3} />
                    <XAxis dataKey="month" tick={{ fill: '#64748b', fontSize: 12 }} />
                    <YAxis tick={{ fill: '#64748b', fontSize: 12 }} width={70} />
                    <Tooltip formatter={fmt} contentStyle={tooltipStyle} />
                    <Line type="monotone" dataKey="amount" name="Spent" stroke="#6366f1" strokeWidth={2.5} dot={{ r: 4 }} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </Card>
            <Card title="Top merchants">
              <div className="h-72" role="img" aria-label={`Top merchants: ${s.per_merchant.map((m) => `${m.merchant} ${fmt(m.amount)}`).join(', ')}`}>
                <ResponsiveContainer>
                  <BarChart data={s.per_merchant} layout="vertical" margin={{ left: 8, right: 16 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#94a3b8" strokeOpacity={0.3} />
                    <XAxis type="number" tick={{ fill: '#64748b', fontSize: 12 }} />
                    <YAxis type="category" dataKey="merchant" width={140} tick={{ fill: '#64748b', fontSize: 12 }} />
                    <Tooltip formatter={fmt} contentStyle={tooltipStyle} />
                    <Bar dataKey="amount" name="Spent" fill="#10b981" radius={[0, 4, 4, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </Card>
            <Card title={`Budgets (${mom?.month})`} action={<Link to="/budgets" className="text-sm font-medium text-indigo-700 hover:underline dark:text-indigo-300">Manage</Link>}>
              {s.budget_status.length === 0 ? (
                <Empty>No budgets set yet.</Empty>
              ) : (
                <ul className="space-y-4">
                  {s.budget_status.map((b) => (
                    <li key={b.category}>
                      <div className="mb-1 flex items-center justify-between text-sm">
                        <span className="font-medium">{b.category}</span>
                        <span className="flex items-center gap-2 tabular-nums">
                          {money(b.spent, cur)} / {money(b.limit, cur)}
                          <Badge tone={b.status === 'exceeded' ? 'red' : b.status === 'warning' ? 'amber' : 'green'}>{b.percentage}%</Badge>
                        </span>
                      </div>
                      <div className="h-2 rounded-full bg-slate-200 dark:bg-slate-800" role="progressbar" aria-label={`${b.category} budget used`} aria-valuenow={Math.min(b.percentage, 100)} aria-valuemin={0} aria-valuemax={100}>
                        <div
                          className={`h-2 rounded-full ${b.status === 'exceeded' ? 'bg-red-500' : b.status === 'warning' ? 'bg-amber-500' : 'bg-emerald-500'}`}
                          style={{ width: `${Math.min(b.percentage, 100)}%` }}
                        />
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          </div>

          <Card title="Biggest purchases">
            <ul className="divide-y divide-slate-100 dark:divide-slate-800">
              {s.top_items.map((it, i) => (
                <li key={i} className="flex items-center justify-between gap-3 py-2 text-sm">
                  <span>
                    <span className="font-medium">{it.item_name}</span>
                    <span className="text-slate-600 dark:text-slate-400"> · {it.store} · {it.date}</span>
                  </span>
                  <span className="flex items-center gap-2">
                    <Badge>{it.category}</Badge>
                    <span className="tabular-nums">{money(it.amount, cur)}</span>
                  </span>
                </li>
              ))}
            </ul>
          </Card>
        </>
      )}
    </div>
  )
}
