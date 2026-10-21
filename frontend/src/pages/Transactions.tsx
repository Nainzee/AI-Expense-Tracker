import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronDown, ChevronUp, Link2, Search, Trash2, Unlink } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useCategories } from '../components/ReceiptEditor'
import StatementTabs from '../components/StatementTabs'
import TxnControls from '../components/TxnControls'
import { Badge, Button, Card, Empty, ErrorBox, inputCls, Spinner } from '../components/ui'
import { api, qs, TXN_TYPE_LABEL, type Transaction, type TxnType } from '../lib/api'
import { money } from '../lib/format'
import { payeeName } from '../lib/statements'

const PAGE = 50
type Candidate = { id: string; date: string; store: string; total: number; currency: string; score: number | null }

function LinkPicker({ txn, onDone }: { txn: Transaction; onDone: () => void }) {
  const qc = useQueryClient()
  const c = useQuery({ queryKey: ['receipt-cands', txn.id], queryFn: () => api<Candidate[]>(`/transactions/${txn.id}/receipt-candidates`) })
  const [error, setError] = useState<unknown>(null)
  const link = async (rid: string) => {
    setError(null)
    try {
      await api(`/transactions/${txn.id}/link`, { method: 'POST', body: JSON.stringify({ receipt_id: rid }) })
      await qc.invalidateQueries()
      onDone()
    } catch (err) {
      setError(err)
    }
  }
  if (c.isLoading) return <Spinner label="Finding receipts" />
  return (
    <div className="space-y-2">
      <ErrorBox error={error ?? c.error} />
      {c.data?.length === 0 && <p className="text-sm text-slate-600 dark:text-slate-400">No unlinked receipts within a week and 25% of this amount.</p>}
      <ul className="space-y-2">
        {c.data?.map((r) => (
          <li key={r.id} className="flex flex-wrap items-center justify-between gap-2 text-sm">
            <span>
              {r.store} · {r.date} · {money(r.total, r.currency)}
            </span>
            <Button variant="secondary" onClick={() => link(r.id)} aria-label={`Link to receipt ${r.store} ${r.date}`}>
              <Link2 className="h-4 w-4" aria-hidden="true" /> Link
            </Button>
          </li>
        ))}
      </ul>
    </div>
  )
}

function TxnRow({ t }: { t: Transaction }) {
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [picking, setPicking] = useState(false)
  const [confirmDel, setConfirmDel] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const call = async (path: string, init: RequestInit) => {
    setError(null)
    try {
      await api(path, init)
      await qc.invalidateQueries()
    } catch (err) {
      setError(err)
    }
  }
  const credit = t.direction === 'credit'
  const panel = `txn-panel-${t.id}`
  return (
    <li className="py-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <p className="truncate font-medium" title={t.description}>{payeeName(t)}</p>
          <p className="truncate text-xs text-slate-600 dark:text-slate-400">{t.date} · {t.description}</p>
          <div className="mt-1 flex flex-wrap gap-1">
            <Badge>{t.txn_type === 'expense' ? t.category : TXN_TYPE_LABEL[t.txn_type]}</Badge>
            {t.receipt && <Badge tone="green">Matched receipt</Badge>}
            {!t.receipt && t.suggestion_count > 0 && <Badge tone="indigo">Possible receipt</Badge>}
            {t.needs_review && <Badge tone="amber">Needs review</Badge>}
            {t.excluded && <Badge tone="red">Excluded</Badge>}
            {!t.counted && !t.excluded && !t.receipt && <Badge>Not spending</Badge>}
          </div>
        </div>
        <div className="flex shrink-0 items-start gap-1">
          <p className={`pt-1 text-right font-semibold whitespace-nowrap tabular-nums ${credit ? 'text-emerald-700 dark:text-emerald-300' : ''}`}>
            {credit ? '+' : '−'}
            {money(t.amount, t.currency)}
            <span className="sr-only">{credit ? ' received' : ' paid'}</span>
          </p>
          <button
            type="button"
            onClick={() => setOpen(!open)}
            aria-expanded={open}
            aria-controls={panel}
            aria-label={`${open ? 'Hide' : 'Edit'} ${payeeName(t)} on ${t.date}`}
            className="inline-flex h-11 w-11 cursor-pointer items-center justify-center rounded-lg text-slate-700 hover:bg-slate-100 dark:text-slate-200 dark:hover:bg-slate-800"
          >
            {open ? <ChevronUp className="h-4 w-4" aria-hidden="true" /> : <ChevronDown className="h-4 w-4" aria-hidden="true" />}
          </button>
        </div>
      </div>
      {open && (
        <div id={panel} className="mt-3 space-y-4 rounded-lg bg-slate-50 p-3 dark:bg-slate-800/60">
          {t.receipt ? (
            <p className="text-sm">
              Matched to{' '}
              <Link to={`/receipts/${encodeURIComponent(t.receipt.id)}`} className="font-medium text-indigo-700 underline dark:text-indigo-300">
                {t.receipt.store} ({t.receipt.date})
              </Link>
              . The receipt is counted instead of this payment.
            </p>
          ) : (
            <TxnControls txn={t} />
          )}
          <ErrorBox error={error} />
          <div className="flex flex-wrap gap-2">
            {t.receipt ? (
              <Button variant="secondary" onClick={() => call(`/transactions/${t.id}/link`, { method: 'DELETE' })}>
                <Unlink className="h-4 w-4" aria-hidden="true" /> Unlink receipt
              </Button>
            ) : (
              !credit && (
                <Button variant="secondary" onClick={() => setPicking(!picking)} aria-expanded={picking}>
                  <Link2 className="h-4 w-4" aria-hidden="true" /> Link a receipt
                </Button>
              )
            )}
            <Button variant="ghost" onClick={() => call(`/transactions/${t.id}`, { method: 'PATCH', body: JSON.stringify({ excluded: !t.excluded }) })}>
              {t.excluded ? 'Include in totals' : 'Exclude from totals'}
            </Button>
            {confirmDel ? (
              <span role="alertdialog" aria-label="Confirm delete transaction" className="flex flex-wrap items-center gap-2">
                <span className="text-sm">Delete this transaction?</span>
                <Button variant="danger" onClick={() => call(`/transactions/${t.id}`, { method: 'DELETE' })}>
                  Yes, delete
                </Button>
                <Button variant="ghost" onClick={() => setConfirmDel(false)}>Cancel</Button>
              </span>
            ) : (
              <Button variant="ghost" onClick={() => setConfirmDel(true)}>
                <Trash2 className="h-4 w-4" aria-hidden="true" /> Delete
              </Button>
            )}
          </div>
          {picking && !t.receipt && <LinkPicker txn={t} onDone={() => setPicking(false)} />}
        </div>
      )}
    </li>
  )
}

export default function Transactions() {
  const cats = useCategories().data?.categories ?? []
  const [q, setQ] = useState('')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [category, setCategory] = useState('')
  const [type, setType] = useState('')
  const [status, setStatus] = useState('')
  const [limit, setLimit] = useState(PAGE)
  const params = qs({ q: q.trim(), from, to, category, txn_type: type, status, limit: String(limit) })
  const list = useQuery({ queryKey: ['transactions', params], queryFn: () => api<{ items: Transaction[]; total: number }>(`/transactions${params}`) })
  const sel = (id: string, label: string, value: string, set: (v: string) => void, opts: [string, string][]) => (
    <div>
      <label htmlFor={id} className="sr-only">{label}</label>
      <select id={id} className={inputCls} value={value} onChange={(e) => { set(e.target.value); setLimit(PAGE) }}>
        {opts.map(([v, l]) => (
          <option key={v} value={v}>{l}</option>
        ))}
      </select>
    </div>
  )

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Bank & UPI statements</h1>
      <StatementTabs />
      <Card>
        <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
          <div className="relative">
            <label htmlFor="t-q" className="sr-only">Search payee or narration</label>
            <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-slate-500" aria-hidden="true" />
            <input id="t-q" className={`${inputCls} pl-9`} placeholder="Search payee" value={q} onChange={(e) => { setQ(e.target.value); setLimit(PAGE) }} />
          </div>
          <div>
            <label htmlFor="t-from" className="sr-only">From date</label>
            <input id="t-from" type="date" className={inputCls} value={from} onChange={(e) => setFrom(e.target.value)} />
          </div>
          <div>
            <label htmlFor="t-to" className="sr-only">To date</label>
            <input id="t-to" type="date" className={inputCls} value={to} onChange={(e) => setTo(e.target.value)} />
          </div>
          {sel('t-cat', 'Category', category, setCategory, [['', 'All categories'], ...cats.map((c): [string, string] => [c, c])])}
          {sel('t-type', 'Type', type, setType, [['', 'All types'], ...(Object.keys(TXN_TYPE_LABEL) as TxnType[]).map((k): [string, string] => [k, TXN_TYPE_LABEL[k]])])}
          {sel('t-status', 'Status', status, setStatus, [
            ['', 'Any status'],
            ['counted', 'Counted as spending'],
            ['not_counted', 'Not counted'],
            ['matched', 'Matched to a receipt'],
            ['suggested', 'Possible receipt match'],
            ['needs_review', 'Needs review'],
            ['excluded', 'Excluded'],
          ])}
        </div>
        {list.isLoading && <Spinner />}
        <ErrorBox error={list.error} />
        {list.data && list.data.total === 0 && (
          <Empty>
            No transactions match. <Link to="/statements" className="font-medium text-indigo-700 underline dark:text-indigo-300">Import a statement</Link>.
          </Empty>
        )}
        {list.data && list.data.items.length > 0 && (
          <>
            <p className="text-xs text-slate-600 dark:text-slate-400" aria-live="polite">
              Showing {list.data.items.length} of {list.data.total}
            </p>
            <ul className="divide-y divide-slate-100 text-sm dark:divide-slate-800">
              {list.data.items.map((t) => (
                <TxnRow key={t.id} t={t} />
              ))}
            </ul>
            {list.data.items.length < list.data.total && (
              <Button variant="secondary" className="mt-3" onClick={() => setLimit(Math.min(limit + PAGE, 500))} loading={list.isFetching}>
                Load more
              </Button>
            )}
          </>
        )}
      </Card>
    </div>
  )
}
