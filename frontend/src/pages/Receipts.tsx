import { useQuery } from '@tanstack/react-query'
import { Download, Search } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useCategories } from '../components/ReceiptEditor'
import { Badge, Button, Card, Empty, ErrorBox, inputCls, Spinner } from '../components/ui'
import { api, qs, request, type ReceiptSummary } from '../lib/api'
import { money } from '../lib/format'

export default function Receipts() {
  const [q, setQ] = useState('')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [category, setCategory] = useState('')
  const [exporting, setExporting] = useState(false)
  const [exportError, setExportError] = useState<unknown>(null)
  const cats = useCategories().data?.categories ?? []
  const params = qs({ q: q.trim(), from, to, category })
  const list = useQuery({ queryKey: ['receipts', params], queryFn: () => api<ReceiptSummary[]>(`/receipts${params}`) })

  const exportCsv = async () => {
    setExporting(true)
    setExportError(null)
    try {
      const res = await request(`/export/csv${qs({ from, to })}`)
      const url = URL.createObjectURL(await res.blob())
      const a = document.createElement('a')
      a.href = url
      a.download = 'expenses.csv'
      a.click()
      URL.revokeObjectURL(url)
    } catch (err) {
      setExportError(err)
    } finally {
      setExporting(false)
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold">Receipts</h1>
        <Button variant="secondary" onClick={exportCsv} loading={exporting}>
          <Download className="h-4 w-4" aria-hidden="true" /> Export CSV
        </Button>
      </div>
      <ErrorBox error={exportError} />
      <Card>
        <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <div className="relative">
            <label htmlFor="r-q" className="sr-only">Search merchant or item</label>
            <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-slate-500" aria-hidden="true" />
            <input id="r-q" className={`${inputCls} pl-9`} placeholder="Search merchant or item" value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
          <div>
            <label htmlFor="r-from" className="sr-only">From date</label>
            <input id="r-from" type="date" className={inputCls} value={from} onChange={(e) => setFrom(e.target.value)} aria-label="From date" />
          </div>
          <div>
            <label htmlFor="r-to" className="sr-only">To date</label>
            <input id="r-to" type="date" className={inputCls} value={to} onChange={(e) => setTo(e.target.value)} aria-label="To date" />
          </div>
          <div>
            <label htmlFor="r-cat" className="sr-only">Category</label>
            <select id="r-cat" className={inputCls} value={category} onChange={(e) => setCategory(e.target.value)}>
              <option value="">All categories</option>
              {cats.map((c) => (
                <option key={c}>{c}</option>
              ))}
            </select>
          </div>
        </div>
        {list.isLoading && <Spinner />}
        <ErrorBox error={list.error} />
        {list.data && list.data.length === 0 && <Empty>No receipts match. <Link to="/upload" className="font-medium text-indigo-700 underline dark:text-indigo-300">Scan one</Link>.</Empty>}
        {list.data && list.data.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <caption className="sr-only">Saved receipts</caption>
              <thead className="border-b border-slate-200 text-slate-600 dark:border-slate-800 dark:text-slate-400">
                <tr>
                  <th scope="col" className="py-2 pr-4 font-medium">Date</th>
                  <th scope="col" className="py-2 pr-4 font-medium">Merchant</th>
                  <th scope="col" className="py-2 pr-4 font-medium">Categories</th>
                  <th scope="col" className="py-2 pr-4 font-medium">Items</th>
                  <th scope="col" className="py-2 pr-4 text-right font-medium">Total</th>
                </tr>
              </thead>
              <tbody>
                {list.data.map((r) => (
                  <tr key={r.id} className="border-b border-slate-100 hover:bg-slate-50 dark:border-slate-800/60 dark:hover:bg-slate-800/40">
                    <td className="py-2 pr-4 whitespace-nowrap">{r.date}</td>
                    <td className="py-2 pr-4">
                      <Link to={`/receipts/${encodeURIComponent(r.id)}`} className="font-medium text-indigo-700 hover:underline dark:text-indigo-300">
                        {r.store}
                      </Link>
                    </td>
                    <td className="py-2 pr-4">
                      <div className="flex flex-wrap gap-1">
                        {r.categories.slice(0, 3).map((c) => (
                          <Badge key={c}>{c}</Badge>
                        ))}
                        {r.categories.length > 3 && <Badge>+{r.categories.length - 3}</Badge>}
                      </div>
                    </td>
                    <td className="py-2 pr-4">{r.item_count}</td>
                    <td className="py-2 pr-4 text-right font-medium tabular-nums">{money(r.total, r.currency)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  )
}
