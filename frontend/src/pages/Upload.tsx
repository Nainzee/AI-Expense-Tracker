import { useQueryClient } from '@tanstack/react-query'
import { Camera, CheckCircle2, FileUp, RotateCcw, Users } from 'lucide-react'
import { useEffect, useRef, useState, type DragEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import FilePreview from '../components/FilePreview'
import ReceiptEditor, { type Draft } from '../components/ReceiptEditor'
import SplitEditor from '../components/SplitEditor'
import { Badge, Button, Card, ErrorBox, Spinner } from '../components/ui'
import { api, ApiError, type BankMatch, type Receipt, type ScanResult } from '../lib/api'
import { money } from '../lib/format'

const ACCEPT = 'image/png,image/jpeg,image/webp,application/pdf'

function toDraft(r: ScanResult): Draft {
  return {
    ...r.extraction,
    notes: '',
    items: r.items.map((i) => ({ ...i, original_category: i.category })),
    upload_id: r.upload_id,
    engine_used: r.engine_used,
    reconciliation_status: r.reconciliation.status,
    raw_text: r.raw_text,
  }
}

export default function UploadPage() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const inputRef = useRef<HTMLInputElement>(null)
  const cameraRef = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<string>()
  const [scan, setScan] = useState<ScanResult | null>(null)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [busy, setBusy] = useState<'scan' | 'save' | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [dup, setDup] = useState<string | null>(null)
  const [dragOver, setDragOver] = useState(false)
  const [saved, setSaved] = useState<Receipt | null>(null)
  const [splitting, setSplitting] = useState(false)
  const [wantSplit, setWantSplit] = useState(false)
  const [bank, setBank] = useState<BankMatch | null>(null)

  useEffect(() => () => { if (preview) URL.revokeObjectURL(preview) }, [preview])

  const reset = () => {
    setFile(null)
    setPreview(undefined)
    setScan(null)
    setDraft(null)
    setError(null)
    setDup(null)
    setSaved(null)
    setSplitting(false)
    setWantSplit(false)
    setBank(null)
  }

  const runScan = async (f: File) => {
    reset()
    setFile(f)
    setPreview(URL.createObjectURL(f))
    setBusy('scan')
    try {
      const form = new FormData()
      form.append('file', f)
      const res = await api<ScanResult>('/scan', { method: 'POST', body: form })
      setScan(res)
      setDraft(toDraft(res))
      setDup(res.duplicate_of)
    } catch (err) {
      setError(err)
    } finally {
      setBusy(null)
    }
  }

  const pick = (files: FileList | null) => {
    const f = files?.[0]
    if (f) void runScan(f)
  }

  const onDrop = (e: DragEvent) => {
    e.preventDefault()
    setDragOver(false)
    pick(e.dataTransfer.files)
  }

  const save = async (force = false) => {
    if (!draft) return
    setBusy('save')
    setError(null)
    try {
      const res = await api<{ id: string; linked_transaction: BankMatch['linked']; match_suggestions: BankMatch['suggestions'] }>('/receipts', {
        method: 'POST',
        body: JSON.stringify({ ...draft, force }),
      })
      setBank({ linked: res.linked_transaction ?? null, suggestions: res.match_suggestions ?? [] })
      await qc.invalidateQueries()
      setSaved(await api<Receipt>(`/receipts/${encodeURIComponent(res.id)}`))
      // If the user already chose "split" on the review screen, go straight to the split form.
      if (wantSplit) setSplitting(true)
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        const d = err.detail as { duplicate_of?: string }
        setDup(d?.duplicate_of ?? 'unknown')
      } else setError(err)
    } finally {
      setBusy(null)
    }
  }

  const recon = scan?.reconciliation
  const openReceipt = () => saved && navigate(`/receipts/${encodeURIComponent(saved.id)}`)

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold">Scan a receipt</h1>
        {file && !saved && (
          <Button variant="secondary" onClick={reset}>
            <RotateCcw className="h-4 w-4" aria-hidden="true" /> Start over
          </Button>
        )}
      </div>

      {!file && (
        <div
          onDragOver={(e) => {
            e.preventDefault()
            setDragOver(true)
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={onDrop}
          className={`flex flex-col items-center justify-center gap-4 rounded-xl border-2 border-dashed p-10 text-center transition-colors ${
            dragOver ? 'border-indigo-500 bg-indigo-50 dark:bg-indigo-950/40' : 'border-slate-300 bg-white dark:border-slate-700 dark:bg-slate-900'
          }`}
        >
          <FileUp className="h-10 w-10 text-indigo-600 dark:text-indigo-400" aria-hidden="true" />
          <div>
            <p className="font-medium">Drop a receipt or bill here</p>
            <p className="text-sm text-slate-600 dark:text-slate-400">PNG, JPEG, WebP or PDF up to 15 MB</p>
          </div>
          <div className="flex flex-wrap justify-center gap-3">
            <Button onClick={() => inputRef.current?.click()}>
              <FileUp className="h-4 w-4" aria-hidden="true" /> Choose file
            </Button>
            <Button variant="secondary" onClick={() => cameraRef.current?.click()}>
              <Camera className="h-4 w-4" aria-hidden="true" /> Use camera
            </Button>
          </div>
          <input ref={inputRef} type="file" accept={ACCEPT} className="sr-only" aria-label="Choose receipt file" onChange={(e) => pick(e.target.files)} />
          <input ref={cameraRef} type="file" accept="image/*" capture="environment" className="sr-only" aria-label="Take a photo of a receipt" onChange={(e) => pick(e.target.files)} />
        </div>
      )}

      <ErrorBox error={error} />

      {saved && bank && (bank.linked || bank.suggestions.length > 0) && (
        <div role="status" className="rounded-lg border border-emerald-300 bg-emerald-50 p-3 text-sm text-emerald-900 dark:border-emerald-800 dark:bg-emerald-950 dark:text-emerald-200">
          {bank.linked ? (
            <>Linked to your bank payment on {bank.linked.date}, it won't be counted twice.</>
          ) : (
            <>
              Possible bank match found.{' '}
              <Link to="/statements/review" className="font-medium underline">Review it</Link>
            </>
          )}
        </div>
      )}

      {saved && (
        <Card title={splitting ? 'Split the bill' : 'Split this bill?'}>
          {splitting ? (
            <SplitEditor receipt={saved} onSaved={openReceipt} onCancel={openReceipt} />
          ) : (
            <div className="space-y-4">
              <p className="text-sm text-slate-700 dark:text-slate-300">
                Saved {saved.store} ({money(saved.total, saved.currency)}). Did you share this bill with anyone?
              </p>
              <div className="flex flex-wrap gap-3">
                <Button onClick={() => setSplitting(true)}>
                  <Users className="h-4 w-4" aria-hidden="true" /> Split bill
                </Button>
                <Button variant="secondary" onClick={openReceipt}>No, just me</Button>
              </div>
            </div>
          )}
        </Card>
      )}

      {file && !saved && (
        <div className="grid gap-6 lg:grid-cols-2">
          <Card title="Original">
            <FilePreview url={preview} isPdf={file.type === 'application/pdf'} />
          </Card>
          <Card
            title="Review & edit"
            action={
              scan && (
                <div className="flex flex-wrap justify-end gap-2">
                  <Badge tone={scan.engine_used === 'tesseract' ? 'slate' : 'indigo'}>{scan.engine_used === 'tesseract' ? 'OCR only' : 'Local AI + OCR'}</Badge>
                  {recon && (
                    <Badge tone={recon.status === 'match' ? 'green' : recon.status === 'mismatch' ? 'red' : 'amber'}>
                      {recon.status === 'match' ? 'Totals match' : recon.status === 'mismatch' ? `Off by ${money(recon.difference, draft?.currency)}` : 'Total not verified'}
                    </Badge>
                  )}
                </div>
              )
            }
          >
            {busy === 'scan' && <Spinner label="Reading your receipt (this can take up to a minute with local AI)" />}
            {scan?.fallback_reason && (
              <p className="mb-4 text-sm text-slate-600 dark:text-slate-400">Used OCR fallback: {scan.fallback_reason}.</p>
            )}
            {dup && (
              <div role="alert" className="mb-4 rounded-lg border border-amber-400 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-200">
                This looks like a receipt you already saved{' '}
                {dup !== 'unknown' && (
                  <Link className="font-medium underline" to={`/receipts/${encodeURIComponent(dup)}`}>
                    ({dup})
                  </Link>
                )}
                . You can still save it as a new receipt.
              </div>
            )}
            {draft && (
              <>
                <SplitChoice value={wantSplit} onChange={setWantSplit} />
                <ReceiptEditor draft={draft} onChange={setDraft} lowFields={scan?.low_confidence_fields} />
                <div className="mt-6">
                  <div className="flex flex-wrap gap-3">
                    <Button onClick={() => save(Boolean(dup))} loading={busy === 'save'} disabled={!draft.merchant_name || draft.items.length === 0}>
                      {wantSplit ? <Users className="h-4 w-4" aria-hidden="true" /> : <CheckCircle2 className="h-4 w-4" aria-hidden="true" />}
                      {dup ? (wantSplit ? 'Save anyway & split' : 'Save anyway') : wantSplit ? 'Save & split bill' : 'Save receipt'}
                    </Button>
                  </div>
                </div>
              </>
            )}
          </Card>
        </div>
      )}
    </div>
  )
}

/** "Split this bill?" toggle shown at the top of the review screen. */
function SplitChoice({ value, onChange, compact = false }: { value: boolean; onChange: (v: boolean) => void; compact?: boolean }) {
  const opt = (v: boolean, label: string) => (
    <button
      type="button"
      role="radio"
      aria-checked={value === v}
      onClick={() => onChange(v)}
      className={`inline-flex min-h-11 items-center gap-2 rounded-lg border px-4 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 ${
        value === v
          ? 'border-indigo-600 bg-indigo-600 text-white'
          : 'border-slate-300 bg-white text-slate-800 hover:bg-slate-50 dark:border-slate-600 dark:bg-slate-900 dark:text-slate-100 dark:hover:bg-slate-800'
      }`}
    >
      {v && <Users className="h-4 w-4" aria-hidden="true" />}
      {label}
    </button>
  )
  return (
    <div
      className={`flex flex-wrap items-center justify-between gap-3 rounded-lg border border-indigo-200 bg-indigo-50 p-3 dark:border-indigo-900 dark:bg-indigo-950/40 ${compact ? '' : 'mb-4'}`}
    >
      <div>
        <p id={compact ? 'split-q-bottom' : 'split-q-top'} className="text-sm font-semibold text-slate-900 dark:text-slate-100">
          Split this bill?
        </p>
        {!compact && (
          <p className="text-xs text-slate-600 dark:text-slate-400">Choose who paid, how many people, and how to divide it after saving.</p>
        )}
      </div>
      <div role="radiogroup" aria-labelledby={compact ? 'split-q-bottom' : 'split-q-top'} className="flex gap-2">
        {opt(true, 'Yes, split')}
        {opt(false, 'No, just me')}
      </div>
    </div>
  )
}
