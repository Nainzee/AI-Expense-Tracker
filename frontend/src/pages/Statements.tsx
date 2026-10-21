import { useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, FileUp, Lock, Repeat, RotateCcw, Trash2, Undo2 } from 'lucide-react'
import { useEffect, useRef, useState, type DragEvent } from 'react'
import { Link } from 'react-router-dom'
import ColumnMapper from '../components/ColumnMapper'
import StatementTabs from '../components/StatementTabs'
import { Badge, Button, Card, Empty, ErrorBox, Field, inputCls, Spinner } from '../components/ui'
import { api, ApiError, TXN_TYPE_LABEL, type ColumnMapping, type ImportSummary, type RecurringPayment, type Statement, type StatementPreview } from '../lib/api'
import { money } from '../lib/format'

const ACCEPT = '.csv,.xlsx,.xls,.pdf,text/csv,application/pdf'

function Stat({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="rounded-lg bg-slate-50 p-3 dark:bg-slate-800/60">
      <dt className="text-xs text-slate-600 dark:text-slate-400">{label}</dt>
      <dd className="text-lg font-semibold tabular-nums">{value}</dd>
    </div>
  )
}

export default function Statements() {
  const qc = useQueryClient()
  const inputRef = useRef<HTMLInputElement>(null)
  const pwRef = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [label, setLabel] = useState('')
  const [password, setPassword] = useState('')
  const [needPassword, setNeedPassword] = useState(false)
  const [preview, setPreview] = useState<StatementPreview | null>(null)
  const [result, setResult] = useState<ImportSummary | null>(null)
  const [busy, setBusy] = useState<'preview' | 'map' | 'commit' | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [dragOver, setDragOver] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState<number | null>(null)
  const list = useQuery({ queryKey: ['statements'], queryFn: () => api<Statement[]>('/statements') })
  const recurring = useQuery({ queryKey: ['recurring'], queryFn: () => api<RecurringPayment[]>('/transactions/recurring') })

  useEffect(() => {
    if (needPassword) pwRef.current?.focus()
  }, [needPassword, error])

  const reset = () => {
    setFile(null)
    setPreview(null)
    setResult(null)
    setError(null)
    setPassword('')
    setNeedPassword(false)
  }

  const runPreview = async (f: File, pw = '') => {
    setFile(f)
    setPreview(null)
    setResult(null)
    setError(null)
    setBusy('preview')
    try {
      const form = new FormData()
      form.append('file', f)
      if (pw) form.append('password', pw)
      if (label.trim()) form.append('account_label', label.trim())
      setPreview(await api<StatementPreview>('/statements/preview', { method: 'POST', body: form }))
      setNeedPassword(false)
    } catch (err) {
      const code = err instanceof ApiError ? (err.detail as { code?: string } | undefined)?.code : undefined
      if (code === 'password_required' || code === 'password_incorrect') setNeedPassword(true)
      setError(err)
    } finally {
      setBusy(null)
    }
  }

  const pick = (files: FileList | null) => {
    const f = files?.[0]
    if (f) {
      setPassword('')
      setNeedPassword(false)
      void runPreview(f)
    }
  }

  const onDrop = (e: DragEvent) => {
    e.preventDefault()
    setDragOver(false)
    pick(e.dataTransfer.files)
  }

  const applyMapping = async (mapping: ColumnMapping) => {
    if (!preview) return
    setBusy('map')
    setError(null)
    try {
      setPreview(await api<StatementPreview>('/statements/reparse', { method: 'POST', body: JSON.stringify({ token: preview.token, mapping }) }))
    } catch (err) {
      setError(err)
    } finally {
      setBusy(null)
    }
  }

  const commit = async () => {
    if (!preview) return
    setBusy('commit')
    setError(null)
    try {
      const res = await api<ImportSummary>('/statements/commit', {
        method: 'POST',
        body: JSON.stringify({ token: preview.token, account_label: label.trim() || null }),
      })
      setResult(res)
      setPreview(null)
      setFile(null)
      await qc.invalidateQueries()
    } catch (err) {
      setError(err)
    } finally {
      setBusy(null)
    }
  }

  const remove = async (id: number) => {
    setError(null)
    try {
      await api(`/statements/${id}`, { method: 'DELETE' })
      setConfirmDelete(null)
      await qc.invalidateQueries()
    } catch (err) {
      setError(err)
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold">Bank & UPI statements</h1>
        {(file || result) && (
          <Button variant="secondary" onClick={reset}>
            <RotateCcw className="h-4 w-4" aria-hidden="true" /> Import another
          </Button>
        )}
      </div>
      <StatementTabs />

      {!file && !result && (
        <Card>
          <div className="mb-4 max-w-sm">
            <Field label="Account name (optional)" htmlFor="st-label" hint={<span className="text-xs text-slate-600 dark:text-slate-400">For example "HDFC savings". Used when the file doesn't say which account it is.</span>}>
              <input id="st-label" className={inputCls} maxLength={60} value={label} onChange={(e) => setLabel(e.target.value)} />
            </Field>
          </div>
          <div
            onDragOver={(e) => {
              e.preventDefault()
              setDragOver(true)
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={onDrop}
            className={`flex flex-col items-center justify-center gap-4 rounded-xl border-2 border-dashed p-8 text-center transition-colors ${
              dragOver ? 'border-indigo-500 bg-indigo-50 dark:bg-indigo-950/40' : 'border-slate-300 dark:border-slate-700'
            }`}
          >
            <FileUp className="h-10 w-10 text-indigo-600 dark:text-indigo-400" aria-hidden="true" />
            <div>
              <p className="font-medium">Drop a bank or UPI statement here</p>
              <p className="text-sm text-slate-600 dark:text-slate-400">CSV, Excel (XLSX/XLS) or text PDF up to 15 MB. Payments you already scanned a receipt for are counted once.</p>
            </div>
            <Button onClick={() => inputRef.current?.click()}>
              <FileUp className="h-4 w-4" aria-hidden="true" /> Choose statement
            </Button>
            <input ref={inputRef} type="file" accept={ACCEPT} className="sr-only" aria-label="Choose statement file" onChange={(e) => pick(e.target.files)} />
          </div>
        </Card>
      )}

      <ErrorBox error={error} />
      {busy === 'preview' && <Spinner label="Reading statement" />}

      {file && needPassword && (
        <Card title="Password protected PDF">
          <form
            className="flex flex-wrap items-end gap-3"
            onSubmit={(e) => {
              e.preventDefault()
              if (password) void runPreview(file, password)
            }}
          >
            <div className="w-full max-w-xs">
              <Field label="PDF password" htmlFor="st-pw" hint={<span className="text-xs text-slate-600 dark:text-slate-400">Used once to open the file. It is never stored.</span>}>
                <input id="st-pw" ref={pwRef} type="password" autoComplete="off" className={inputCls} value={password} onChange={(e) => setPassword(e.target.value)} />
              </Field>
            </div>
            <Button type="submit" disabled={!password} loading={busy === 'preview'}>
              <Lock className="h-4 w-4" aria-hidden="true" /> Unlock
            </Button>
          </form>
        </Card>
      )}

      {preview && (
        <Card
          title={preview.filename}
          action={
            <div className="flex flex-wrap justify-end gap-2">
              <Badge tone="indigo">{preview.format.toUpperCase()}</Badge>
              {preview.source !== 'generic' && <Badge>{preview.source.toUpperCase()}</Badge>}
              {preview.account_hint && <Badge>{preview.account_hint}</Badge>}
            </div>
          }
        >
          {preview.already_imported && (
            <p role="status" className="mb-4 rounded-lg border border-amber-400 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-200">
              You imported this exact file before. Only rows that aren't saved yet will be added.
            </p>
          )}
          {preview.needs_mapping ? (
            <ColumnMapper preview={preview} busy={busy === 'map'} onApply={applyMapping} />
          ) : (
            <div className="space-y-4">
              <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                <Stat label="Transactions" value={preview.rows_parsed} />
                <Stat label="New" value={preview.likely_new} />
                <Stat label="Already saved" value={preview.likely_duplicates} />
                <Stat label="Period" value={preview.period_start ? `${preview.period_start} – ${preview.period_end}` : '—'} />
              </dl>
              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <caption className="sr-only">First transactions in this statement</caption>
                  <thead className="border-b border-slate-200 text-slate-600 dark:border-slate-800 dark:text-slate-400">
                    <tr>
                      <th scope="col" className="py-2 pr-3 font-medium">Date</th>
                      <th scope="col" className="py-2 pr-3 font-medium">Payee</th>
                      <th scope="col" className="py-2 pr-3 font-medium">Type</th>
                      <th scope="col" className="py-2 text-right font-medium">Amount</th>
                    </tr>
                  </thead>
                  <tbody>
                    {preview.transactions.slice(0, 15).map((t, i) => (
                      <tr key={i} className="border-b border-slate-100 dark:border-slate-800/60">
                        <td className="py-2 pr-3 whitespace-nowrap">{t.date}</td>
                        <td className="max-w-56 truncate py-2 pr-3" title={t.description}>{t.merchant || t.description}</td>
                        <td className="py-2 pr-3"><Badge>{t.txn_type === 'expense' ? t.category : TXN_TYPE_LABEL[t.txn_type]}</Badge></td>
                        <td className={`py-2 text-right whitespace-nowrap tabular-nums ${t.direction === 'credit' ? 'text-emerald-700 dark:text-emerald-300' : ''}`}>
                          {t.direction === 'credit' ? '+' : '−'}
                          {money(t.amount)}
                          <span className="sr-only">{t.direction === 'credit' ? ' received' : ' paid'}</span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {preview.rows_parsed > 15 && <p className="text-xs text-slate-600 dark:text-slate-400">Showing 15 of {preview.rows_parsed}.</p>}
              <Button onClick={commit} loading={busy === 'commit'} disabled={preview.rows_parsed === 0}>
                <CheckCircle2 className="h-4 w-4" aria-hidden="true" /> Import {preview.likely_new} new transaction{preview.likely_new === 1 ? '' : 's'}
              </Button>
            </div>
          )}
        </Card>
      )}

      {result && (
        <Card title="Import complete">
          <div role="status" className="space-y-4">
            <dl className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
              <Stat label="New" value={result.new} />
              <Stat label="Duplicates skipped" value={result.duplicates_skipped} />
              <Stat label="Auto-categorised" value={result.auto_categorized} />
              <Stat label="Matched to receipts" value={result.matched_receipts} />
              <Stat label="Possible matches" value={result.suggested_matches} />
              <Stat label="Needs review" value={result.needs_review} />
            </dl>
            {result.new === 0 && <p className="text-sm">Everything in this file was already saved, so nothing changed.</p>}
            <div className="flex flex-wrap gap-3 text-sm">
              <Link to="/statements/transactions" className="font-medium text-indigo-700 underline dark:text-indigo-300">View transactions</Link>
              {result.needs_review + result.suggested_matches > 0 && (
                <Link to="/statements/review" className="font-medium text-indigo-700 underline dark:text-indigo-300">Review {result.needs_review + result.suggested_matches} items</Link>
              )}
            </div>
            {result.statement_id != null && result.new > 0 && (
              <Button
                variant="secondary"
                onClick={async () => {
                  await remove(result.statement_id as number)
                  setResult(null)
                }}
              >
                <Undo2 className="h-4 w-4" aria-hidden="true" /> Undo this import
              </Button>
            )}
          </div>
        </Card>
      )}

      <div className="grid gap-6 lg:grid-cols-2">
        <Card title="Imported statements">
          {list.isLoading && <Spinner />}
          {list.data && list.data.length === 0 && <Empty>No statements imported yet.</Empty>}
          <ul className="divide-y divide-slate-100 dark:divide-slate-800">
            {list.data?.map((s) => (
              <li key={s.id} className="flex flex-wrap items-center justify-between gap-3 py-3 text-sm">
                <div className="min-w-0">
                  <p className="truncate font-medium">{s.account_label || s.filename}</p>
                  <p className="text-slate-600 dark:text-slate-400">
                    {s.source.toUpperCase()} {s.account_hint ?? ''} · {s.period_start} – {s.period_end} · {s.txn_count} transactions
                  </p>
                </div>
                {confirmDelete === s.id ? (
                  <div role="alertdialog" aria-label="Confirm delete statement" className="flex flex-wrap items-center gap-2">
                    <span>Delete it and its {s.txn_count} transactions?</span>
                    <Button variant="danger" onClick={() => remove(s.id)}>Delete</Button>
                    <Button variant="ghost" onClick={() => setConfirmDelete(null)} autoFocus>Cancel</Button>
                  </div>
                ) : (
                  <Button variant="secondary" className="min-h-11" onClick={() => setConfirmDelete(s.id)} aria-label={`Undo import of ${s.filename}`}>
                    <Trash2 className="h-4 w-4" aria-hidden="true" /> Undo import
                  </Button>
                )}
              </li>
            ))}
          </ul>
        </Card>
        <Card title="Recurring payments">
          {recurring.data && recurring.data.length === 0 && <Empty>No subscriptions or regular bills detected yet.</Empty>}
          <ul className="divide-y divide-slate-100 dark:divide-slate-800">
            {recurring.data?.map((r) => (
              <li key={r.payee_key} className="flex items-center justify-between gap-3 py-3 text-sm">
                <div className="flex min-w-0 items-center gap-2">
                  <Repeat className="h-4 w-4 shrink-0 text-indigo-600 dark:text-indigo-400" aria-hidden="true" />
                  <div className="min-w-0">
                    <p className="truncate font-medium">{r.merchant}</p>
                    <p className="text-slate-600 dark:text-slate-400">{r.cadence === 'monthly' ? 'Monthly' : 'Weekly'} · next around {r.next_expected}</p>
                  </div>
                </div>
                <span className="whitespace-nowrap tabular-nums">{money(r.average_amount, r.currency)}</span>
              </li>
            ))}
          </ul>
        </Card>
      </div>
    </div>
  )
}
