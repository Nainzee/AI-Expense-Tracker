import { useState } from 'react'
import type { ColumnMapping, StatementPreview } from '../lib/api'
import { Button, Field, inputCls } from './ui'

type Role = 'date' | 'description' | 'debit' | 'credit' | 'amount' | 'drcr' | 'balance' | 'reference'
const ROLES: { key: Role; label: string; required?: boolean }[] = [
  { key: 'date', label: 'Date', required: true },
  { key: 'description', label: 'Description / narration', required: true },
  { key: 'debit', label: 'Debit (money out)' },
  { key: 'credit', label: 'Credit (money in)' },
  { key: 'amount', label: 'Single amount column' },
  { key: 'drcr', label: 'Dr / Cr indicator' },
  { key: 'balance', label: 'Balance' },
  { key: 'reference', label: 'Reference / UTR' },
]

const start = (p: StatementPreview): ColumnMapping =>
  p.mapping ?? { header_row: -1, date: 0, description: 1, debit: 2, credit: null, amount: null, drcr: null, balance: null, reference: null, date_order: 'dmy' }

/** Manual column mapping, shown only when the header could not be detected confidently. */
export default function ColumnMapper({ preview, busy, onApply }: { preview: StatementPreview; busy: boolean; onApply: (m: ColumnMapping) => void }) {
  const [m, setM] = useState<ColumnMapping>(() => start(preview))
  const width = Math.max(...preview.sample_rows.map((r) => r.length), 1)
  const header = m.header_row >= 0 ? preview.sample_rows[m.header_row] ?? [] : []
  const colName = (i: number) => `${i + 1}: ${(header[i] || preview.sample_rows.find((r, idx) => idx > m.header_row && r[i])?.[i] || '').slice(0, 28)}`
  const used = ROLES.map((r) => m[r.key]).filter((v): v is number => v !== null && v !== undefined)
  const valid = m.date !== undefined && m.description !== undefined && [m.debit, m.credit, m.amount].some((v) => v !== null && v !== undefined) && new Set(used).size === used.length

  return (
    <div className="space-y-4">
      <p className="text-sm text-slate-700 dark:text-slate-300">We couldn't recognise this statement's columns. Tell us which column is which.</p>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <Field label="Header row" htmlFor="map-header">
          <select id="map-header" className={inputCls} value={m.header_row} onChange={(e) => setM({ ...m, header_row: Number(e.target.value) })}>
            <option value={-1}>No header row</option>
            {preview.sample_rows.map((r, i) => (
              <option key={i} value={i}>
                Row {i + 1}: {r.join(' | ').slice(0, 40)}
              </option>
            ))}
          </select>
        </Field>
        {ROLES.map((r) => (
          <Field key={r.key} label={`${r.label}${r.required ? ' (required)' : ''}`} htmlFor={`map-${r.key}`}>
            <select
              id={`map-${r.key}`}
              className={inputCls}
              value={m[r.key] ?? ''}
              onChange={(e) => setM({ ...m, [r.key]: e.target.value === '' ? null : Number(e.target.value) })}
            >
              {!r.required && <option value="">Not in this file</option>}
              {Array.from({ length: width }, (_, i) => (
                <option key={i} value={i}>
                  Column {colName(i)}
                </option>
              ))}
            </select>
          </Field>
        ))}
        <Field label="Date format" htmlFor="map-order">
          <select id="map-order" className={inputCls} value={m.date_order} onChange={(e) => setM({ ...m, date_order: e.target.value as ColumnMapping['date_order'] })}>
            <option value="dmy">Day / month / year</option>
            <option value="mdy">Month / day / year</option>
            <option value="ymd">Year-month-day</option>
          </select>
        </Field>
      </div>
      {!valid && <p className="text-sm text-amber-800 dark:text-amber-300">Pick date, description and a debit/credit or amount column. Each column can be used once.</p>}
      <Button onClick={() => onApply(m)} disabled={!valid} loading={busy}>
        Apply columns
      </Button>
    </div>
  )
}
