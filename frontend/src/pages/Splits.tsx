import { useQuery, useQueryClient } from '@tanstack/react-query'
import { HandCoins } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Badge, Button, Card, Empty, ErrorBox, Field, inputCls, Spinner } from '../components/ui'
import { api, type Balances, type SplitSummary } from '../lib/api'
import { isoDate, money } from '../lib/format'

type PersonBalance = Balances['people'][number]

function SettleForm({ b, onDone }: { b: PersonBalance; onDone: () => void }) {
  const qc = useQueryClient()
  const [amount, setAmount] = useState(String(Math.abs(b.net)))
  const [date, setDate] = useState(() => isoDate(new Date()))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const theyOwe = b.net > 0
  const id = `settle-${b.person_id}-${b.currency}`
  const value = Number(amount)
  const ok = Number.isFinite(value) && value > 0

  const submit = async () => {
    setBusy(true)
    setError(null)
    try {
      await api('/settlements', {
        method: 'POST',
        body: JSON.stringify({ person_id: b.person_id, direction: theyOwe ? 'they_paid' : 'you_paid', amount: value, currency: b.currency, date }),
      })
      await qc.invalidateQueries()
      onDone()
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form
      className="mt-3 space-y-3 rounded-lg bg-slate-50 p-3 dark:bg-slate-800/60"
      onSubmit={(e) => {
        e.preventDefault()
        if (ok) void submit()
      }}
    >
      <p className="text-sm">{theyOwe ? `Record a payment from ${b.name} to you.` : `Record a payment from you to ${b.name}.`}</p>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label={`Amount (${b.currency})`} htmlFor={`${id}-amt`}>
          <input id={`${id}-amt`} type="number" min={0} step="any" inputMode="decimal" className={inputCls} value={amount} onChange={(e) => setAmount(e.target.value)} />
        </Field>
        <Field label="Date" htmlFor={`${id}-date`}>
          <input id={`${id}-date`} type="date" className={inputCls} value={date} onChange={(e) => setDate(e.target.value)} />
        </Field>
      </div>
      <ErrorBox error={error} />
      <div className="flex flex-wrap gap-2">
        <Button type="submit" loading={busy} disabled={!ok}>Record payment</Button>
        <Button type="button" variant="ghost" onClick={onDone}>Cancel</Button>
      </div>
    </form>
  )
}

export default function Splits() {
  const bal = useQuery({ queryKey: ['split-balances'], queryFn: () => api<Balances>('/splits/balances') })
  const list = useQuery({ queryKey: ['splits'], queryFn: () => api<SplitSummary[]>('/splits') })
  const [settling, setSettling] = useState<string | null>(null)

  if (bal.isLoading || list.isLoading) return <Spinner />
  const b = bal.data
  const splits = list.data ?? []

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Splits</h1>
      <ErrorBox error={bal.error ?? list.error} />

      {b && b.totals.length > 0 && (
        <div className="grid gap-4 sm:grid-cols-2">
          {b.totals.map((t) => (
            <div key={t.currency} className="contents">
              <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900">
                <p className="text-sm text-slate-600 dark:text-slate-400">You are owed{b.totals.length > 1 ? ` (${t.currency})` : ''}</p>
                <p className="mt-1 text-2xl font-semibold tabular-nums text-emerald-700 dark:text-emerald-300">{money(t.owed_to_you, t.currency)}</p>
              </div>
              <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900">
                <p className="text-sm text-slate-600 dark:text-slate-400">You owe{b.totals.length > 1 ? ` (${t.currency})` : ''}</p>
                <p className="mt-1 text-2xl font-semibold tabular-nums text-amber-800 dark:text-amber-300">{money(t.you_owe, t.currency)}</p>
              </div>
            </div>
          ))}
        </div>
      )}

      <Card title="Balances">
        {b && b.people.length === 0 ? (
          <Empty>{splits.length ? 'All settled up. Nobody owes anything.' : 'No split bills yet. Scan a receipt and choose Split bill.'}</Empty>
        ) : (
          <ul className="divide-y divide-slate-100 dark:divide-slate-800">
            {b?.people.map((p) => {
              const key = `${p.person_id}-${p.currency}`
              const owesYou = p.net > 0
              return (
                <li key={key} className="py-3">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <p className="text-sm">
                      {owesYou ? (
                        <>
                          <span className="font-medium">{p.name}</span> owes you{' '}
                          <span className="font-semibold tabular-nums text-emerald-700 dark:text-emerald-300">{money(p.net, p.currency)}</span>
                        </>
                      ) : (
                        <>
                          You owe <span className="font-medium">{p.name}</span>{' '}
                          <span className="font-semibold tabular-nums text-amber-800 dark:text-amber-300">{money(-p.net, p.currency)}</span>
                        </>
                      )}
                    </p>
                    {settling !== key && (
                      <Button variant="secondary" onClick={() => setSettling(key)} aria-label={`Settle up with ${p.name} (${p.currency})`}>
                        <HandCoins className="h-4 w-4" aria-hidden="true" /> Settle up
                      </Button>
                    )}
                  </div>
                  {settling === key && <SettleForm b={p} onDone={() => setSettling(null)} />}
                </li>
              )
            })}
          </ul>
        )}
      </Card>

      {splits.length > 0 && (
        <Card title="Split bills">
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <caption className="sr-only">Split bills</caption>
              <thead className="border-b border-slate-200 text-slate-600 dark:border-slate-800 dark:text-slate-400">
                <tr>
                  <th scope="col" className="py-2 pr-4 font-medium">Date</th>
                  <th scope="col" className="py-2 pr-4 font-medium">Merchant</th>
                  <th scope="col" className="py-2 pr-4 font-medium">Paid by</th>
                  <th scope="col" className="py-2 pr-4 text-right font-medium">Total</th>
                  <th scope="col" className="py-2 text-right font-medium">You</th>
                </tr>
              </thead>
              <tbody>
                {splits.map((s) => (
                  <tr key={s.receipt_id} className="border-b border-slate-100 dark:border-slate-800/60">
                    <td className="py-2 pr-4 whitespace-nowrap">{s.date}</td>
                    <td className="py-2 pr-4">
                      <Link to={`/receipts/${encodeURIComponent(s.receipt_id)}`} className="font-medium text-indigo-700 hover:underline dark:text-indigo-300">
                        {s.store}
                      </Link>
                    </td>
                    <td className="py-2 pr-4"><Badge>{s.payer}</Badge></td>
                    <td className="py-2 pr-4 text-right tabular-nums">{money(s.total, s.currency)}</td>
                    <td className="py-2 text-right whitespace-nowrap tabular-nums">
                      {s.you_lent > 0 ? (
                        <span className="text-emerald-700 dark:text-emerald-300">lent {money(s.you_lent, s.currency)}</span>
                      ) : s.you_owe > 0 ? (
                        <span className="text-amber-800 dark:text-amber-300">owe {money(s.you_owe, s.currency)}</span>
                      ) : (
                        <span className="text-slate-600 dark:text-slate-400">nothing</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  )
}
