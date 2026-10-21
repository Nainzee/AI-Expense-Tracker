import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Check, Landmark, Link2, Pencil, Trash2, Unlink, Users, X } from 'lucide-react'
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import FilePreview from '../components/FilePreview'
import ReceiptEditor, { type Draft } from '../components/ReceiptEditor'
import SplitEditor from '../components/SplitEditor'
import { Badge, Button, Card, ErrorBox, Spinner } from '../components/ui'
import { api, type Receipt, type SplitMethod, type TxnBrief } from '../lib/api'
import { money } from '../lib/format'

const METHOD_LABEL: Record<SplitMethod, string> = { equal: 'equally', exact: 'exact amounts', percent: 'percentages', shares: 'shares', items: 'by item' }

function toDraft(r: Receipt): Draft {
  return {
    merchant_name: r.store,
    merchant_address: r.merchant_address,
    date: r.date,
    time: r.time,
    invoice_number: r.invoice_number,
    currency: r.currency,
    subtotal: r.subtotal || null,
    tax: r.tax || null,
    total: r.total,
    payment_method: r.payment_method || null,
    notes: r.notes,
    items: r.items.map((i) => ({ item_name: i.item_name, amount: i.amount, category: i.category, quantity: i.quantity, unit_price: i.unit_price, discount: i.discount, original_category: i.category })),
  }
}

function BankMatchCard({ r }: { r: Receipt }) {
  const qc = useQueryClient()
  const [picking, setPicking] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const cands = useQuery({
    queryKey: ['bank-cands', r.id],
    queryFn: () => api<{ transaction: TxnBrief; score: number | null }[]>(`/receipts/${encodeURIComponent(r.id)}/bank-candidates`),
    enabled: picking,
  })
  const act = async (path: string, init: RequestInit) => {
    setError(null)
    try {
      await api(path, init)
      await qc.invalidateQueries()
      setPicking(false)
    } catch (err) {
      setError(err)
    }
  }
  const link = (tid: number) => act(`/transactions/${tid}/link`, { method: 'POST', body: JSON.stringify({ receipt_id: r.id }) })
  const bm = r.bank_match
  const row = (t: TxnBrief) => `${t.merchant || t.description} · ${t.date} · ${money(t.amount, t.currency)}`
  return (
    <Card title="Bank match" action={bm?.linked && <Badge tone="green">Counted once</Badge>}>
      <ErrorBox error={error} />
      {bm?.linked ? (
        <div className="flex flex-wrap items-center justify-between gap-3 text-sm">
          <p className="flex items-center gap-2">
            <Landmark className="h-4 w-4 shrink-0 text-emerald-700 dark:text-emerald-300" aria-hidden="true" />
            <span>Paid via bank: {row(bm.linked)}. Only this receipt is counted.</span>
          </p>
          <Button variant="secondary" onClick={() => act(`/transactions/${bm.linked!.id}/link`, { method: 'DELETE' })}>
            <Unlink className="h-4 w-4" aria-hidden="true" /> Unlink
          </Button>
        </div>
      ) : (
        <div className="space-y-3 text-sm">
          {bm?.suggestions.map((s) => (
            <div key={s.transaction.id} className="flex flex-wrap items-center justify-between gap-3">
              <span>Is this the same payment? {row(s.transaction)}</span>
              <div className="flex gap-2">
                <Button onClick={() => link(s.transaction.id)}>
                  <Check className="h-4 w-4" aria-hidden="true" /> Same
                </Button>
                <Button variant="secondary" onClick={() => act(`/transactions/${s.transaction.id}/candidates/${encodeURIComponent(r.id)}/reject`, { method: 'POST' })}>
                  <X className="h-4 w-4" aria-hidden="true" /> Not the same
                </Button>
              </div>
            </div>
          ))}
          {!bm?.suggestions.length && <p className="text-slate-600 dark:text-slate-400">Not matched to a bank or UPI payment.</p>}
          <Button variant="ghost" onClick={() => setPicking(!picking)} aria-expanded={picking}>
            <Link2 className="h-4 w-4" aria-hidden="true" /> Link a bank payment
          </Button>
          {picking && cands.isLoading && <Spinner label="Finding payments" />}
          {picking && cands.data?.length === 0 && <p className="text-slate-600 dark:text-slate-400">No unlinked payments within a week and 25% of this total.</p>}
          {picking && (
            <ul className="space-y-2">
              {cands.data?.map((c) => (
                <li key={c.transaction.id} className="flex flex-wrap items-center justify-between gap-2">
                  <span>{row(c.transaction)}</span>
                  <Button variant="secondary" onClick={() => link(c.transaction.id)} aria-label={`Link ${row(c.transaction)}`}>
                    Link
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </Card>
  )
}

export default function ReceiptDetail() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const path = `/receipts/${encodeURIComponent(id)}`
  const rec = useQuery({ queryKey: ['receipt', id], queryFn: () => api<Receipt>(path) })
  const [draft, setDraft] = useState<Draft | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [editSplit, setEditSplit] = useState(false)
  const [confirmUnsplit, setConfirmUnsplit] = useState(false)
  const [splitRemoved, setSplitRemoved] = useState(false)

  const save = async () => {
    if (!draft) return
    setBusy(true)
    setError(null)
    try {
      const res = await api<{ split_removed?: boolean }>(path, { method: 'PUT', body: JSON.stringify(draft) })
      setSplitRemoved(Boolean(res.split_removed))
      await qc.invalidateQueries()
      setDraft(null)
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  const remove = async () => {
    setBusy(true)
    try {
      await api(path, { method: 'DELETE' })
      await qc.invalidateQueries()
      navigate('/receipts')
    } catch (err) {
      setError(err)
      setBusy(false)
    }
  }

  const unsplit = async () => {
    setBusy(true)
    setError(null)
    try {
      await api(`${path}/split`, { method: 'DELETE' })
      await qc.invalidateQueries()
      setConfirmUnsplit(false)
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  if (rec.isLoading) return <Spinner />
  if (rec.error || !rec.data) return <ErrorBox error={rec.error ?? 'Receipt not found'} />
  const r = rec.data

  return (
    <div className="space-y-6">
      <Link to="/receipts" className="inline-flex items-center gap-1 text-sm font-medium text-indigo-700 hover:underline dark:text-indigo-300">
        <ArrowLeft className="h-4 w-4" aria-hidden="true" /> All receipts
      </Link>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">{r.store}</h1>
          <p className="text-sm text-slate-600 dark:text-slate-400">
            {r.date}
            {r.time ? ` · ${r.time}` : ''} · {money(r.total, r.currency)} · {r.id}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {!draft && (
            <Button variant="secondary" onClick={() => setDraft(toDraft(r))}>
              <Pencil className="h-4 w-4" aria-hidden="true" /> Edit
            </Button>
          )}
          {!confirmDelete ? (
            <Button variant="secondary" onClick={() => setConfirmDelete(true)}>
              <Trash2 className="h-4 w-4" aria-hidden="true" /> Delete
            </Button>
          ) : (
            <div role="alertdialog" aria-label="Confirm delete" className="flex items-center gap-2">
              <span className="text-sm">Delete this receipt?</span>
              <Button variant="danger" onClick={remove} loading={busy}>Delete</Button>
              <Button variant="ghost" onClick={() => setConfirmDelete(false)} autoFocus>Cancel</Button>
            </div>
          )}
        </div>
      </div>
      <ErrorBox error={error} />
      {splitRemoved && (
        <div role="status" className="rounded-lg border border-amber-400 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-200">
          The split was removed because it no longer matched the edited receipt.
        </div>
      )}
      <BankMatchCard r={r} />
      <Card
        title="Split"
        action={r.split && !editSplit && <Badge tone="indigo">Split · {METHOD_LABEL[r.split.method]}</Badge>}
      >
        {editSplit ? (
          <SplitEditor receipt={r} initial={r.split} onSaved={() => { setEditSplit(false); setSplitRemoved(false) }} onCancel={() => setEditSplit(false)} />
        ) : r.split ? (
          <div className="space-y-4">
            <p className="text-sm font-semibold">
              {r.split.payer.id === null ? (
                <span className="text-emerald-700 dark:text-emerald-300">You paid · you lent {money(r.split.you_lent, r.split.currency)}</span>
              ) : (
                <span className="text-amber-800 dark:text-amber-300">{r.split.payer.name} paid · you owe {money(r.split.you_owe, r.split.currency)} to {r.split.payer.name}</span>
              )}
            </p>
            <ul className="divide-y divide-slate-100 text-sm dark:divide-slate-800">
              {r.split.shares.map((s) => (
                <li key={s.person_id ?? 'you'} className="flex justify-between gap-3 py-2">
                  <span>{s.name}</span>
                  <span className="tabular-nums">{money(s.amount, r.split!.currency)}</span>
                </li>
              ))}
            </ul>
            <div className="flex flex-wrap gap-2">
              <Button variant="secondary" onClick={() => setEditSplit(true)}>
                <Pencil className="h-4 w-4" aria-hidden="true" /> Edit split
              </Button>
              {!confirmUnsplit ? (
                <Button variant="secondary" onClick={() => setConfirmUnsplit(true)}>
                  <Trash2 className="h-4 w-4" aria-hidden="true" /> Remove split
                </Button>
              ) : (
                <div role="alertdialog" aria-label="Confirm remove split" className="flex flex-wrap items-center gap-2">
                  <span className="text-sm">Remove this split? Balances will update.</span>
                  <Button variant="danger" onClick={unsplit} loading={busy}>Remove</Button>
                  <Button variant="ghost" onClick={() => setConfirmUnsplit(false)} autoFocus>Cancel</Button>
                </div>
              )}
            </div>
          </div>
        ) : (
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-sm text-slate-600 dark:text-slate-400">Shared this bill? Split it to track who owes what.</p>
            <Button variant="secondary" onClick={() => setEditSplit(true)}>
              <Users className="h-4 w-4" aria-hidden="true" /> Split this bill
            </Button>
          </div>
        )}
      </Card>
      <div className="grid gap-6 lg:grid-cols-2">
        <Card title="Original">
          <FilePreview receiptId={r.has_file ? r.id : undefined} />
        </Card>
        {draft ? (
          <Card title="Edit receipt">
            <ReceiptEditor draft={draft} onChange={setDraft} />
            <div className="mt-6 flex gap-3">
              <Button onClick={save} loading={busy}>Save changes</Button>
              <Button variant="ghost" onClick={() => setDraft(null)}>Cancel</Button>
            </div>
          </Card>
        ) : (
          <Card
            title="Details"
            action={
              <div className="flex gap-2">
                {r.engine_used && <Badge tone="indigo">{r.engine_used === 'tesseract' ? 'OCR' : 'Local AI + OCR'}</Badge>}
                {r.reconciliation_status && (
                  <Badge tone={r.reconciliation_status === 'match' ? 'green' : r.reconciliation_status === 'mismatch' ? 'red' : 'amber'}>
                    {r.reconciliation_status === 'match' ? 'Totals match' : r.reconciliation_status}
                  </Badge>
                )}
              </div>
            }
          >
            <dl className="mb-4 grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
              {[
                ['Address', r.merchant_address],
                ['Invoice', r.invoice_number],
                ['Payment', r.payment_method],
                ['Subtotal', r.subtotal ? money(r.subtotal, r.currency) : null],
                ['Tax', r.tax ? money(r.tax, r.currency) : null],
                ['Notes', r.notes],
              ]
                .filter(([, v]) => v)
                .map(([k, v]) => (
                  <div key={k as string}>
                    <dt className="text-slate-600 dark:text-slate-400">{k}</dt>
                    <dd className="font-medium">{v}</dd>
                  </div>
                ))}
            </dl>
            <table className="w-full text-left text-sm">
              <caption className="sr-only">Line items</caption>
              <thead className="border-b border-slate-200 text-slate-600 dark:border-slate-800 dark:text-slate-400">
                <tr>
                  <th scope="col" className="py-2 pr-2 font-medium">Item</th>
                  <th scope="col" className="py-2 pr-2 font-medium">Category</th>
                  <th scope="col" className="py-2 text-right font-medium">Amount</th>
                </tr>
              </thead>
              <tbody>
                {r.items.map((it) => (
                  <tr key={it.id} className="border-b border-slate-100 dark:border-slate-800/60">
                    <td className="py-2 pr-2">{it.item_name}</td>
                    <td className="py-2 pr-2"><Badge>{it.category}</Badge></td>
                    <td className="py-2 text-right tabular-nums">{money(it.amount, r.currency)}</td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr>
                  <th scope="row" colSpan={2} className="py-2 text-right font-semibold">Total</th>
                  <td className="py-2 text-right font-semibold tabular-nums">{money(r.total, r.currency)}</td>
                </tr>
              </tfoot>
            </table>
          </Card>
        )}
      </div>
    </div>
  )
}
