import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Save, UserPlus } from 'lucide-react'
import { useId, useMemo, useState, type KeyboardEvent } from 'react'
import { api, type Person, type Receipt, type Split, type SplitIn, type SplitMethod } from '../lib/api'
import { money } from '../lib/format'
import { computeShares, fromMinor, toMinor } from '../lib/split'
import { Button, ErrorBox, Field, inputCls } from './ui'

const METHODS: { id: SplitMethod; label: string; hint: string }[] = [
  { id: 'equal', label: 'Equally', hint: 'Everyone pays the same.' },
  { id: 'exact', label: 'Exact amounts', hint: 'Enter what each person pays. Amounts must add up to the total.' },
  { id: 'percent', label: 'Percentages', hint: 'Enter each person’s percentage. They must add up to 100.' },
  { id: 'shares', label: 'Shares', hint: 'Split by ratio, e.g. 2 shares vs 1 share.' },
  { id: 'items', label: 'By item', hint: 'Tick who had each item. Shared items are split equally; tax and fees are spread in proportion.' },
]
const MAX_PEOPLE = 20
const RESERVED = new Set(['you', 'me'])

interface Row {
  name: string
  value: string
}

const norm = (s: string) => s.trim().replace(/\s+/g, ' ')

function initialState(split: Split | null | undefined) {
  if (!split) return { includeMe: true, rows: [{ name: 'You', value: '' }, { name: '', value: '' }], payer: 0, method: 'equal' as SplitMethod, excluded: new Set<string>() }
  const me = split.shares.find((s) => s.person_id === null)
  const others = split.shares.filter((s) => s.person_id !== null)
  const val = (v: number | null | undefined) => (v === null || v === undefined ? '' : String(v))
  const rows: Row[] = [{ name: 'You', value: val(me?.value) }, ...others.map((s) => ({ name: s.name, value: val(s.value) }))]
  const rowOf = (id: number | null) => (id === null ? 0 : 1 + others.findIndex((s) => s.person_id === id))
  const excluded = new Set<string>()
  for (const a of split.items) for (let r = 0; r < rows.length; r++) {
    const id = r === 0 ? null : others[r - 1].person_id
    if (!a.person_ids.includes(id)) excluded.add(`${a.item_index}:${r}`)
  }
  if (rows.length < 2) rows.push({ name: '', value: '' })
  return { includeMe: Boolean(me), rows, payer: rowOf(split.payer.id), method: split.method, excluded }
}

export default function SplitEditor({ receipt, initial, onSaved, onCancel }: { receipt: Receipt; initial?: Split | null; onSaved: (s: Split) => void; onCancel: () => void }) {
  const uid = useId()
  const qc = useQueryClient()
  const peopleQ = useQuery({ queryKey: ['people'], queryFn: () => api<Person[]>('/people') })
  const people = useMemo(() => peopleQ.data ?? [], [peopleQ.data])
  const [init] = useState(() => initialState(initial))
  const [includeMe, setIncludeMe] = useState(init.includeMe)
  const [rows, setRows] = useState<Row[]>(init.rows)
  const [countText, setCountText] = useState(String(init.rows.length))
  const [payer, setPayer] = useState(init.payer)
  const [method, setMethod] = useState<SplitMethod>(init.method)
  const [excluded, setExcluded] = useState<Set<string>>(init.excluded)
  const [busy, setBusy] = useState(false)
  const [adding, setAdding] = useState<number | null>(null)
  const [error, setError] = useState<unknown>(null)

  const cur = receipt.currency
  const totalMinor = toMinor(receipt.total, cur)
  const findPerson = (n: string) => people.find((p) => p.name.toLowerCase() === norm(n).toLowerCase())

  const resize = (text: string) => {
    setCountText(text)
    const n = Math.floor(Number(text))
    if (!Number.isFinite(n) || n < 2 || n > MAX_PEOPLE) return
    setRows((rs) => (n > rs.length ? [...rs, ...Array.from({ length: n - rs.length }, () => ({ name: '', value: '' }))] : rs.slice(0, n)))
    if (payer >= n) setPayer(0)
  }
  const setRow = (i: number, patch: Partial<Row>) => setRows((rs) => rs.map((r, k) => (k === i ? { ...r, ...patch } : r)))

  // Active rows are the people sharing the cost (You only when ticked).
  const active = rows.map((_, i) => i).filter((i) => i > 0 || includeMe)

  const nameErrors = rows.map((r, i) => {
    if (i === 0) return null
    const n = norm(r.name)
    if (!n) return 'Enter a name.'
    if (n.length > 60) return 'Use 60 characters or fewer.'
    if (RESERVED.has(n.toLowerCase())) return '“You” is already in the split. Use another name.'
    if (rows.some((o, k) => k > 0 && k < i && norm(o.name).toLowerCase() === n.toLowerCase())) return 'This person is already in the split.'
    return null
  })

  const values = active.map((i) => {
    const raw = rows[i].value.trim()
    if (raw === '' || !Number.isFinite(Number(raw))) return null
    return method === 'exact' ? toMinor(Number(raw), cur) : Number(raw)
  })
  const itemsForPreview = receipt.items.map((it, idx) => ({
    minor: toMinor(it.amount, cur),
    rows: active.map((r, k) => (excluded.has(`${idx}:${r}`) ? -1 : k)).filter((k) => k >= 0),
  }))
  const preview = computeShares(method, totalMinor, values, itemsForPreview)
  const amountFor = (i: number) => {
    const k = active.indexOf(i)
    return k >= 0 && preview.amounts ? preview.amounts[k] : null
  }
  const yourMinor = includeMe ? (amountFor(0) ?? null) : 0
  const payerName = payer === 0 ? 'You' : norm(rows[payer]?.name ?? '') || `Person ${payer + 1}`

  let formError: string | null = null
  if (totalMinor <= 0) formError = 'This receipt needs a total above zero before it can be split.'
  else if (nameErrors.some(Boolean)) formError = 'Fix the names above.'
  else if (!includeMe && payer !== 0) formError = 'You must be the payer or one of the people sharing the bill.'
  else if (preview.error) formError = preview.error
  const valid = formError === null && preview.amounts !== null

  const addPerson = async (i: number) => {
    setAdding(i)
    setError(null)
    try {
      await api<Person>('/people', { method: 'POST', body: JSON.stringify({ name: norm(rows[i].name) }) })
      await qc.invalidateQueries({ queryKey: ['people'] })
    } catch (err) {
      setError(err)
    } finally {
      setAdding(null)
    }
  }

  const save = async () => {
    if (!valid) return
    setBusy(true)
    setError(null)
    try {
      const ids: (number | null)[] = [null]
      for (let i = 1; i < rows.length; i++) {
        const existing = findPerson(rows[i].name)
        ids.push(existing ? existing.id : (await api<Person>('/people', { method: 'POST', body: JSON.stringify({ name: norm(rows[i].name) }) })).id)
      }
      const body: SplitIn = {
        payer_id: ids[payer],
        method,
        participants: active.map((i) => ({ person_id: ids[i], value: method === 'equal' || method === 'items' ? null : Number(rows[i].value) })),
        items: method === 'items' ? receipt.items.map((_, idx) => ({ item_index: idx, person_ids: active.filter((r) => !excluded.has(`${idx}:${r}`)).map((r) => ids[r]) })) : [],
      }
      const split = await api<Split>(`/receipts/${encodeURIComponent(receipt.id)}/split`, { method: 'PUT', body: JSON.stringify(body) })
      await qc.invalidateQueries()
      onSaved(split)
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  const onTabKey = (e: KeyboardEvent<HTMLButtonElement>, idx: number) => {
    if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return
    e.preventDefault()
    const next = (idx + (e.key === 'ArrowRight' ? 1 : METHODS.length - 1)) % METHODS.length
    setMethod(METHODS[next].id)
    document.getElementById(`${uid}-tab-${METHODS[next].id}`)?.focus()
  }

  const valueLabel = method === 'exact' ? `Amount (${cur})` : method === 'percent' ? 'Percent' : 'Shares'
  const showValue = method === 'exact' || method === 'percent' || method === 'shares'
  const listId = `${uid}-people`

  return (
    <div className="space-y-5">
      <p className="text-sm text-slate-600 dark:text-slate-400">
        Splitting {money(receipt.total, cur)} from {receipt.store}.
      </p>

      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Number of people" htmlFor={`${uid}-count`} hint={<span className="text-xs text-slate-600 dark:text-slate-400">Including you. Between 2 and {MAX_PEOPLE}.</span>}>
          <input id={`${uid}-count`} type="number" min={2} max={MAX_PEOPLE} inputMode="numeric" className={inputCls} value={countText} onChange={(e) => resize(e.target.value)} />
        </Field>
        <Field label="Paid by" htmlFor={`${uid}-payer`}>
          <select id={`${uid}-payer`} className={inputCls} value={payer} onChange={(e) => setPayer(Number(e.target.value))}>
            <option value={0}>You</option>
            {rows.map((r, i) => (i > 0 && norm(r.name) ? <option key={i} value={i}>{norm(r.name)}</option> : null))}
          </select>
        </Field>
      </div>

      <div>
        <div role="tablist" aria-label="Split method" className="flex flex-wrap gap-1 rounded-lg bg-slate-100 p-1 dark:bg-slate-800">
          {METHODS.map((m, idx) => (
            <button
              key={m.id}
              id={`${uid}-tab-${m.id}`}
              type="button"
              role="tab"
              aria-selected={method === m.id}
              aria-controls={`${uid}-panel`}
              tabIndex={method === m.id ? 0 : -1}
              onClick={() => setMethod(m.id)}
              onKeyDown={(e) => onTabKey(e, idx)}
              className={`min-h-10 cursor-pointer rounded-md px-3 py-2 text-sm font-medium transition-colors ${
                method === m.id ? 'bg-white text-indigo-700 shadow-sm dark:bg-slate-950 dark:text-indigo-300' : 'text-slate-700 hover:bg-white/60 dark:text-slate-300 dark:hover:bg-slate-700'
              }`}
            >
              {m.label}
            </button>
          ))}
        </div>
        <p className="mt-2 text-sm text-slate-600 dark:text-slate-400">{METHODS.find((m) => m.id === method)?.hint}</p>
      </div>

      <div id={`${uid}-panel`} role="tabpanel" aria-labelledby={`${uid}-tab-${method}`} className="space-y-4">
        <datalist id={listId}>
          {people.map((p) => (
            <option key={p.id} value={p.name} />
          ))}
        </datalist>
        <ul className="space-y-3">
          {rows.map((r, i) => {
            const amt = amountFor(i)
            const err = nameErrors[i]
            const n = norm(r.name)
            const canAdd = i > 0 && n && !err && !findPerson(n)
            return (
              <li key={i} className="flex flex-wrap items-end gap-3 rounded-lg border border-slate-200 p-3 dark:border-slate-800">
                {i === 0 ? (
                  <div className="flex min-h-10 min-w-40 flex-1 items-center gap-2">
                    <input id={`${uid}-me`} type="checkbox" className="h-5 w-5 cursor-pointer accent-indigo-600" checked={includeMe} onChange={(e) => setIncludeMe(e.target.checked)} />
                    <label htmlFor={`${uid}-me`} className="cursor-pointer text-sm font-medium">
                      You <span className="font-normal text-slate-600 dark:text-slate-400">(include me)</span>
                    </label>
                  </div>
                ) : (
                  <div className="flex min-w-40 basis-full flex-col gap-1 sm:basis-auto sm:flex-1">
                    <label htmlFor={`${uid}-name-${i}`} className="text-xs font-medium text-slate-700 dark:text-slate-300">Person {i + 1}</label>
                    <div className="flex gap-2">
                      <input
                        id={`${uid}-name-${i}`}
                        list={listId}
                        autoComplete="off"
                        className={inputCls}
                        value={r.name}
                        placeholder="Name"
                        aria-invalid={Boolean(err && r.name)}
                        aria-describedby={err && r.name ? `${uid}-name-err-${i}` : undefined}
                        onChange={(e) => setRow(i, { name: e.target.value })}
                      />
                      {canAdd && (
                        <Button type="button" variant="secondary" className="shrink-0 px-3" loading={adding === i} onClick={() => addPerson(i)} aria-label={`Add ${n} to your people`}>
                          <UserPlus className="h-4 w-4" aria-hidden="true" /> Add
                        </Button>
                      )}
                    </div>
                    {err && r.name && (
                      <span id={`${uid}-name-err-${i}`} className="text-xs font-medium text-red-700 dark:text-red-300">{err}</span>
                    )}
                  </div>
                )}
                {showValue && (i > 0 || includeMe) && (
                  <div className="flex w-32 flex-col gap-1">
                    <label htmlFor={`${uid}-val-${i}`} className="text-xs font-medium text-slate-700 dark:text-slate-300">
                      {valueLabel} <span className="sr-only">for {i === 0 ? 'you' : n || `person ${i + 1}`}</span>
                    </label>
                    <input id={`${uid}-val-${i}`} type="number" min={0} step={method === 'exact' ? 'any' : method === 'percent' ? '0.01' : '1'} inputMode="decimal" className={inputCls} value={r.value} onChange={(e) => setRow(i, { value: e.target.value })} />
                  </div>
                )}
                <div className="ml-auto min-w-24 text-right text-sm">
                  <span className="block text-xs text-slate-600 dark:text-slate-400">Share</span>
                  <span className="font-semibold tabular-nums">{i === 0 && !includeMe ? money(0, cur) : amt === null ? '—' : money(fromMinor(amt, cur), cur)}</span>
                </div>
              </li>
            )
          })}
        </ul>

        {method === 'items' && (
          <fieldset className="space-y-3">
            <legend className="mb-2 text-sm font-semibold">Who had what</legend>
            {receipt.items.length === 0 && <p className="text-sm text-slate-600 dark:text-slate-400">This receipt has no line items.</p>}
            {receipt.items.map((it, idx) => (
              <div key={it.id} className="rounded-lg border border-slate-200 p-3 dark:border-slate-800">
                <div className="mb-2 flex justify-between gap-3 text-sm">
                  <span className="font-medium">{it.item_name}</span>
                  <span className="tabular-nums">{money(it.amount, cur)}</span>
                </div>
                <div className="flex flex-wrap gap-x-4 gap-y-2">
                  {active.map((r) => {
                    const who = r === 0 ? 'You' : norm(rows[r].name) || `Person ${r + 1}`
                    const key = `${idx}:${r}`
                    const cid = `${uid}-it-${idx}-${r}`
                    return (
                      <div key={r} className="flex min-h-10 items-center gap-2">
                        <input
                          id={cid}
                          type="checkbox"
                          className="h-5 w-5 cursor-pointer accent-indigo-600"
                          checked={!excluded.has(key)}
                          aria-label={`${it.item_name} for ${who}`}
                          onChange={(e) =>
                            setExcluded((s) => {
                              const next = new Set(s)
                              if (e.target.checked) next.delete(key)
                              else next.add(key)
                              return next
                            })
                          }
                        />
                        <label htmlFor={cid} className="cursor-pointer text-sm">{who}</label>
                      </div>
                    )
                  })}
                </div>
              </div>
            ))}
          </fieldset>
        )}

        <div aria-live="polite" className="space-y-1 rounded-lg bg-slate-50 p-3 text-sm dark:bg-slate-800/60">
          {method === 'exact' && preview.remaining !== null && (
            <p className={preview.remaining === 0 ? 'text-emerald-700 dark:text-emerald-300' : 'text-amber-800 dark:text-amber-300'}>
              Remaining: {money(fromMinor(preview.remaining, cur), cur)}
            </p>
          )}
          {method === 'percent' && preview.remaining !== null && (
            <p className={preview.remaining === 0 ? 'text-emerald-700 dark:text-emerald-300' : 'text-amber-800 dark:text-amber-300'}>{preview.remaining}% left</p>
          )}
          {valid && yourMinor !== null ? (
            <p className="font-semibold">
              {payer === 0 ? (
                <span className="text-emerald-700 dark:text-emerald-300">You lent {money(fromMinor(totalMinor - yourMinor, cur), cur)}</span>
              ) : (
                <span className="text-amber-800 dark:text-amber-300">You owe {money(fromMinor(yourMinor, cur), cur)} to {payerName}</span>
              )}
            </p>
          ) : (
            formError && <p className="text-slate-700 dark:text-slate-300">{formError}</p>
          )}
        </div>
      </div>

      <ErrorBox error={error} />
      <div className="flex flex-wrap gap-3">
        <Button onClick={save} loading={busy} disabled={!valid}>
          <Save className="h-4 w-4" aria-hidden="true" /> Save split
        </Button>
        <Button variant="ghost" onClick={onCancel}>Cancel</Button>
      </div>
    </div>
  )
}
