import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Pencil, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import FilePreview from '../components/FilePreview'
import ReceiptEditor, { type Draft } from '../components/ReceiptEditor'
import { Badge, Button, Card, ErrorBox, Spinner } from '../components/ui'
import { api, type Receipt } from '../lib/api'
import { money } from '../lib/format'

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

  const save = async () => {
    if (!draft) return
    setBusy(true)
    setError(null)
    try {
      await api(path, { method: 'PUT', body: JSON.stringify(draft) })
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
