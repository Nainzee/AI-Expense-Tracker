import { useEffect, useState } from 'react'
import { request } from '../lib/api'
import { Empty, Spinner } from './ui'

/** Shows an image or PDF from a local object URL, or fetches an authenticated receipt file. */
export default function FilePreview({ url, isPdf, receiptId }: { url?: string; isPdf?: boolean; receiptId?: string }) {
  const [remote, setRemote] = useState<{ url: string; pdf: boolean } | null>(null)
  const [state, setState] = useState<'idle' | 'loading' | 'missing'>(receiptId ? 'loading' : 'idle')

  useEffect(() => {
    if (!receiptId) return
    let objectUrl = ''
    let cancelled = false
    request(`/receipts/${encodeURIComponent(receiptId)}/file`)
      .then(async (res) => {
        const blob = await res.blob()
        if (cancelled) return
        objectUrl = URL.createObjectURL(blob)
        setRemote({ url: objectUrl, pdf: blob.type === 'application/pdf' })
        setState('idle')
      })
      .catch(() => !cancelled && setState('missing'))
    return () => {
      cancelled = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [receiptId])

  const src = url ?? remote?.url
  const pdf = url ? isPdf : remote?.pdf
  if (state === 'loading') return <Spinner label="Loading receipt image" />
  if (state === 'missing' || !src) return <Empty>No source file stored for this receipt.</Empty>
  return pdf ? (
    <iframe src={src} title="Receipt PDF" className="h-[70vh] w-full rounded-lg border border-slate-200 dark:border-slate-800" />
  ) : (
    <img src={src} alt="Uploaded receipt" className="max-h-[75vh] w-full rounded-lg border border-slate-200 object-contain dark:border-slate-800" />
  )
}
