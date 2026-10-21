import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, TXN_TYPE_LABEL, type Transaction, type TxnType } from '../lib/api'
import { payeeName } from '../lib/statements'
import { useCategories } from './ReceiptEditor'
import { Button, ErrorBox, Field, inputCls } from './ui'

/** Category + type editor with "just this one" vs "all from this payee" (remembered for future imports). */
export default function TxnControls({ txn, onDone }: { txn: Transaction; onDone?: () => void }) {
  const qc = useQueryClient()
  const cats = useCategories().data?.categories ?? []
  const [category, setCategory] = useState(txn.category)
  const [type, setType] = useState<TxnType>(txn.txn_type)
  const [busy, setBusy] = useState<'one' | 'all' | null>(null)
  const [error, setError] = useState<unknown>(null)
  const id = `txn-${txn.id}`

  const save = async (all: boolean) => {
    setBusy(all ? 'all' : 'one')
    setError(null)
    try {
      await api(`/transactions/${txn.id}`, { method: 'PATCH', body: JSON.stringify({ category, txn_type: type, apply_to_payee: all }) })
      await qc.invalidateQueries()
      onDone?.()
    } catch (err) {
      setError(err)
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Category" htmlFor={`${id}-cat`}>
          <select id={`${id}-cat`} className={inputCls} value={category} onChange={(e) => setCategory(e.target.value)}>
            {cats.map((c) => (
              <option key={c}>{c}</option>
            ))}
          </select>
        </Field>
        <Field label="Type" htmlFor={`${id}-type`}>
          <select id={`${id}-type`} className={inputCls} value={type} onChange={(e) => setType(e.target.value as TxnType)}>
            {(Object.keys(TXN_TYPE_LABEL) as TxnType[]).map((k) => (
              <option key={k} value={k}>
                {TXN_TYPE_LABEL[k]}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <ErrorBox error={error} />
      <div className="flex flex-wrap gap-2">
        <Button onClick={() => save(false)} loading={busy === 'one'} disabled={busy !== null}>
          Just this one
        </Button>
        {txn.payee_key && (
          <Button variant="secondary" onClick={() => save(true)} loading={busy === 'all'} disabled={busy !== null}>
            All from {payeeName(txn)}
          </Button>
        )}
      </div>
    </div>
  )
}
