import { useQuery } from '@tanstack/react-query'
import { Plus, Trash2 } from 'lucide-react'
import { api, type Item, type ReceiptPayload } from '../lib/api'
import { money } from '../lib/format'
import { inputCls } from './ui'

export type Draft = ReceiptPayload

const CURRENCIES = ['INR', 'USD', 'EUR', 'GBP']

const lowCls = 'border-amber-500 bg-amber-50 dark:border-amber-500 dark:bg-amber-950/40'

function numOrNull(v: string): number | null {
  if (v.trim() === '') return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}

export function useCategories() {
  return useQuery({ queryKey: ['categories'], queryFn: () => api<{ categories: string[] }>('/categories'), staleTime: Infinity })
}

function LowHint({ show }: { show: boolean }) {
  return show ? <span className="text-xs font-medium text-amber-800 dark:text-amber-300">Low confidence, please check</span> : null
}

export default function ReceiptEditor({ draft, onChange, lowFields = [] }: { draft: Draft; onChange: (d: Draft) => void; lowFields?: string[] }) {
  const cats = useCategories().data?.categories ?? []
  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => onChange({ ...draft, [k]: v })
  const setItem = (i: number, patch: Partial<Item>) =>
    onChange({ ...draft, items: draft.items.map((it, idx) => (idx === i ? { ...it, ...patch, low_confidence: patch.amount !== undefined || patch.item_name !== undefined ? false : it.low_confidence } : it)) })
  const low = (f: string) => lowFields.includes(f)
  const itemsSum = draft.items.reduce((s, it) => s + (Number(it.amount) || 0), 0)

  const text = (id: keyof Draft, label: string, type = 'text') => (
    <div className="flex flex-col gap-1">
      <label htmlFor={`f-${id}`} className="text-sm font-medium text-slate-700 dark:text-slate-300">{label}</label>
      <input
        id={`f-${id}`}
        type={type}
        className={`${inputCls} ${low(id) ? lowCls : ''}`}
        value={(draft[id] as string | null) ?? ''}
        onChange={(e) => set(id, (e.target.value || null) as never)}
        aria-describedby={low(id) ? `f-${id}-low` : undefined}
      />
      <span id={`f-${id}-low`}><LowHint show={low(id)} /></span>
    </div>
  )

  const num = (id: 'subtotal' | 'tax' | 'total', label: string) => (
    <div className="flex flex-col gap-1">
      <label htmlFor={`f-${id}`} className="text-sm font-medium text-slate-700 dark:text-slate-300">{label}</label>
      <input
        id={`f-${id}`}
        type="number"
        step="0.01"
        inputMode="decimal"
        className={`${inputCls} ${low(id) ? lowCls : ''}`}
        value={draft[id] ?? ''}
        onChange={(e) => set(id, numOrNull(e.target.value))}
      />
      <LowHint show={low(id)} />
    </div>
  )

  return (
    <div className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="flex flex-col gap-1 sm:col-span-2">
          <label htmlFor="f-merchant" className="text-sm font-medium text-slate-700 dark:text-slate-300">Merchant</label>
          <input id="f-merchant" required className={`${inputCls} ${low('merchant_name') ? lowCls : ''}`} value={draft.merchant_name} onChange={(e) => set('merchant_name', e.target.value)} />
          <LowHint show={low('merchant_name')} />
        </div>
        {text('date', 'Date', 'date')}
        {text('time', 'Time', 'time')}
        {text('invoice_number', 'Invoice number')}
        {text('payment_method', 'Payment method')}
        <div className="sm:col-span-2">{text('merchant_address', 'Address')}</div>
        <div className="flex flex-col gap-1">
          <label htmlFor="f-currency" className="text-sm font-medium text-slate-700 dark:text-slate-300">Currency</label>
          <select id="f-currency" className={inputCls} value={draft.currency} onChange={(e) => set('currency', e.target.value)}>
            {Array.from(new Set([draft.currency, ...CURRENCIES])).map((c) => (
              <option key={c}>{c}</option>
            ))}
          </select>
        </div>
        {num('subtotal', 'Subtotal')}
        {num('tax', 'Tax')}
        {num('total', 'Total')}
      </div>

      <div>
        <div className="mb-2 flex items-center justify-between">
          <h3 className="text-sm font-semibold">Line items ({draft.items.length})</h3>
          <span className="text-sm text-slate-600 dark:text-slate-400">Items sum: {money(itemsSum, draft.currency)}</span>
        </div>
        <ul className="space-y-3">
          {draft.items.map((it, i) => (
            <li key={i} className={`grid grid-cols-12 items-end gap-2 rounded-lg border p-3 ${it.low_confidence ? lowCls : 'border-slate-200 dark:border-slate-800'}`}>
              <div className="col-span-12 flex flex-col gap-1 sm:col-span-5">
                <label htmlFor={`it-name-${i}`} className="text-xs font-medium text-slate-700 dark:text-slate-300">Item {i + 1}</label>
                <input id={`it-name-${i}`} className={inputCls} value={it.item_name} onChange={(e) => setItem(i, { item_name: e.target.value })} />
              </div>
              <div className="col-span-6 flex flex-col gap-1 sm:col-span-3">
                <label htmlFor={`it-cat-${i}`} className="text-xs font-medium text-slate-700 dark:text-slate-300">Category</label>
                <select id={`it-cat-${i}`} className={inputCls} value={it.category} onChange={(e) => setItem(i, { category: e.target.value })}>
                  {(cats.length ? cats : [it.category]).map((c) => (
                    <option key={c}>{c}</option>
                  ))}
                </select>
              </div>
              <div className="col-span-4 flex flex-col gap-1 sm:col-span-3">
                <label htmlFor={`it-amt-${i}`} className="text-xs font-medium text-slate-700 dark:text-slate-300">Amount</label>
                <input id={`it-amt-${i}`} type="number" step="0.01" inputMode="decimal" className={inputCls} value={Number.isFinite(it.amount) ? it.amount : ''} onChange={(e) => setItem(i, { amount: Number(e.target.value) })} />
              </div>
              <div className="col-span-2 flex justify-end sm:col-span-1">
                <button
                  type="button"
                  aria-label={`Remove item ${i + 1}`}
                  onClick={() => onChange({ ...draft, items: draft.items.filter((_, idx) => idx !== i) })}
                  className="inline-flex h-10 w-10 cursor-pointer items-center justify-center rounded-lg text-slate-600 hover:bg-red-50 hover:text-red-700 dark:text-slate-300 dark:hover:bg-red-950"
                >
                  <Trash2 className="h-4 w-4" aria-hidden="true" />
                </button>
              </div>
              {(it.low_confidence || it.category_confidence !== undefined) && (
                <div className="col-span-12 flex flex-wrap gap-3 text-xs text-slate-600 dark:text-slate-400">
                  {it.low_confidence && <LowHint show />}
                  {it.category_confidence !== undefined && <span>Category confidence {(it.category_confidence * 100).toFixed(0)}%{it.category_source === 'llm_hint' ? ' (AI suggestion)' : ''}</span>}
                </div>
              )}
            </li>
          ))}
        </ul>
        <button
          type="button"
          onClick={() => onChange({ ...draft, items: [...draft.items, { item_name: '', amount: 0, category: 'Other' }] })}
          className="mt-3 inline-flex min-h-10 cursor-pointer items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium text-indigo-700 hover:bg-indigo-50 dark:text-indigo-300 dark:hover:bg-indigo-950"
        >
          <Plus className="h-4 w-4" aria-hidden="true" /> Add item
        </button>
      </div>

      <div className="flex flex-col gap-1">
        <label htmlFor="f-notes" className="text-sm font-medium text-slate-700 dark:text-slate-300">Notes</label>
        <textarea id="f-notes" rows={2} className={inputCls} value={draft.notes ?? ''} onChange={(e) => set('notes', e.target.value)} />
      </div>
    </div>
  )
}
