import { useQueryClient } from '@tanstack/react-query'
import { Check, X } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useCategories } from '../components/ReceiptEditor'
import StatementTabs from '../components/StatementTabs'
import { Badge, Button, Card, Empty, ErrorBox, Spinner } from '../components/ui'
import { api, TXN_TYPE_LABEL, type Transaction } from '../lib/api'
import { money } from '../lib/format'
import { payeeName, useReviewQueue } from '../lib/statements'

const QUICK = ['Food', 'Dining', 'Transportation', 'Utilities', 'Medical', 'Entertainment', 'Household', 'Other']

function ReviewItem({ t }: { t: Transaction }) {
  const qc = useQueryClient()
  const cats = useCategories().data?.categories ?? []
  const [all, setAll] = useState(true)
  const [error, setError] = useState<unknown>(null)
  const person = t.txn_type === 'transfer_person'
  const send = async (body: Record<string, unknown>) => {
    setError(null)
    try {
      await api(`/transactions/${t.id}`, { method: 'PATCH', body: JSON.stringify({ ...body, apply_to_payee: all && Boolean(t.payee_key) }) })
      await qc.invalidateQueries()
    } catch (err) {
      setError(err)
    }
  }
  const allId = `rev-all-${t.id}`
  return (
    <li className="space-y-2 py-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate font-medium" title={t.description}>{payeeName(t)}</p>
          <p className="truncate text-xs text-slate-600 dark:text-slate-400">{t.date} · {t.description}</p>
          <p className="mt-1 text-xs">
            {person ? 'Looks like a payment to a person. Was it spending (what for?) or a transfer?' : `Currently ${t.category}. Pick the right category.`}
          </p>
        </div>
        <span className="pt-1 font-semibold whitespace-nowrap tabular-nums">{money(t.amount, t.currency)}</span>
      </div>
      <div className="flex flex-wrap gap-2" role="group" aria-label={`Category for ${payeeName(t)}`}>
        {[...QUICK.filter((c) => cats.length === 0 || cats.includes(c))].map((c) => (
          <button
            key={c}
            type="button"
            onClick={() => send({ category: c, txn_type: 'expense' })}
            className={`min-h-11 rounded-full border px-3 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 ${
              c === t.category && !person
                ? 'border-indigo-600 bg-indigo-50 text-indigo-800 dark:bg-indigo-950 dark:text-indigo-200'
                : 'border-slate-300 bg-white text-slate-800 hover:bg-slate-50 dark:border-slate-600 dark:bg-slate-900 dark:text-slate-100 dark:hover:bg-slate-800'
            }`}
          >
            {c}
          </button>
        ))}
        {person && (
          <Button variant="secondary" className="min-h-11" onClick={() => send({ category: 'Other', txn_type: 'transfer_person' })}>
            {TXN_TYPE_LABEL.transfer_person} (keep)
          </Button>
        )}
        <Button variant="ghost" className="min-h-11" onClick={() => send({ txn_type: 'transfer_self', category: 'Other' })}>
          Not spending
        </Button>
      </div>
      {t.payee_key && (
        <div className="flex items-center gap-2">
          <input id={allId} type="checkbox" className="h-4 w-4" checked={all} onChange={(e) => setAll(e.target.checked)} />
          <label htmlFor={allId} className="text-sm">Apply to all payments to {payeeName(t)} (and remember it)</label>
        </div>
      )}
      <ErrorBox error={error} />
    </li>
  )
}

export default function StatementReview() {
  const qc = useQueryClient()
  const q = useReviewQueue()
  const [error, setError] = useState<unknown>(null)
  const act = async (path: string, init: RequestInit) => {
    setError(null)
    try {
      await api(path, init)
      await qc.invalidateQueries()
    } catch (err) {
      setError(err)
    }
  }
  const data = q.data

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Bank & UPI statements</h1>
      <StatementTabs />
      {q.isLoading && <Spinner />}
      <ErrorBox error={q.error ?? error} />
      {data && data.count === 0 && (
        <Card>
          <Empty>All caught up. Nothing needs your review.</Empty>
        </Card>
      )}
      {data && data.match_suggestions.length > 0 && (
        <Card title="Is this the same payment?">
          <p className="mb-3 text-sm text-slate-600 dark:text-slate-400">Confirming keeps the receipt and stops the bank payment from being counted twice.</p>
          <ul className="divide-y divide-slate-100 dark:divide-slate-800">
            {data.match_suggestions.map((s) => (
              <li key={`${s.transaction.id}-${s.receipt.id}`} className="flex flex-wrap items-center justify-between gap-3 py-3 text-sm">
                <div className="grid min-w-0 flex-1 gap-1 sm:grid-cols-2">
                  <p className="min-w-0">
                    <span className="block text-xs text-slate-600 dark:text-slate-400">Bank payment</span>
                    <span className="block truncate font-medium">{payeeName(s.transaction)}</span>
                    {s.transaction.date} · {money(s.transaction.amount, s.transaction.currency)}
                  </p>
                  <p className="min-w-0">
                    <span className="block text-xs text-slate-600 dark:text-slate-400">Receipt</span>
                    <Link to={`/receipts/${encodeURIComponent(s.receipt.id)}`} className="block truncate font-medium text-indigo-700 underline dark:text-indigo-300">
                      {s.receipt.store}
                    </Link>
                    {s.receipt.date} · {money(s.receipt.total, s.receipt.currency)}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <Badge tone="indigo">{Math.round(s.score * 100)}% match</Badge>
                  <Button onClick={() => act(`/transactions/${s.transaction.id}/link`, { method: 'POST', body: JSON.stringify({ receipt_id: s.receipt.id }) })}>
                    <Check className="h-4 w-4" aria-hidden="true" /> Same payment
                  </Button>
                  <Button variant="secondary" onClick={() => act(`/transactions/${s.transaction.id}/candidates/${encodeURIComponent(s.receipt.id)}/reject`, { method: 'POST' })}>
                    <X className="h-4 w-4" aria-hidden="true" /> Not the same
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        </Card>
      )}
      {data && data.needs_review.length > 0 && (
        <Card title={`Check these payments (${data.needs_review.length})`}>
          <ul className="divide-y divide-slate-100 text-sm dark:divide-slate-800">
            {data.needs_review.map((t) => (
              <ReviewItem key={t.id} t={t} />
            ))}
          </ul>
        </Card>
      )}
    </div>
  )
}
