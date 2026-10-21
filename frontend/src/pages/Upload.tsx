import { useQueryClient } from '@tanstack/react-query'
import { Camera, CheckCircle2, FileUp, RotateCcw } from 'lucide-react'
import { useEffect, useRef, useState, type DragEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import FilePreview from '../components/FilePreview'
import ReceiptEditor, { type Draft } from '../components/ReceiptEditor'
import { Badge, Button, Card, ErrorBox, Spinner } from '../components/ui'
import { api, ApiError, type ScanResult } from '../lib/api'
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

  useEffect(() => () => { if (preview) URL.revokeObjectURL(preview) }, [preview])

  const reset = () => {
    setFile(null)
    setPreview(undefined)
    setScan(null)
    setDraft(null)
    setError(null)
    setDup(null)
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
      const res = await api<{ id: string }>('/receipts', { method: 'POST', body: JSON.stringify({ ...draft, force }) })
      await qc.invalidateQueries()
      navigate(`/receipts/${encodeURIComponent(res.id)}`)
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

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold">Scan a receipt</h1>
        {file && (
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

      {file && (
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
                <ReceiptEditor draft={draft} onChange={setDraft} lowFields={scan?.low_confidence_fields} />
                <div className="mt-6 flex flex-wrap gap-3">
                  <Button onClick={() => save(Boolean(dup))} loading={busy === 'save'} disabled={!draft.merchant_name || draft.items.length === 0}>
                    <CheckCircle2 className="h-4 w-4" aria-hidden="true" /> {dup ? 'Save anyway' : 'Save receipt'}
                  </Button>
                </div>
              </>
            )}
          </Card>
        </div>
      )}
    </div>
  )
}
