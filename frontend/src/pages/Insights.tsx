import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, Lightbulb, RefreshCw, Send } from 'lucide-react'
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Badge, Button, Card, Empty, ErrorBox, inputCls, Spinner } from '../components/ui'
import { api, qs, type ChatReply, type Insights as InsightsT } from '../lib/api'
import { currentMonth } from '../lib/format'

interface Msg {
  role: 'user' | 'assistant'
  content: string
  source?: string
  tools?: string[]
}

const SUGGESTIONS = ['How much did I spend this month?', 'Where can I cut back?', 'Which merchant do I spend the most at?', 'Am I within my budgets?']

function Chat() {
  const [messages, setMessages] = useState<Msg[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const endRef = useRef<HTMLDivElement>(null)

  // Braces matter: scrollIntoView may return a Promise in newer browsers, and an
  // effect must return nothing or a cleanup function, otherwise React crashes.
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: 'nearest' })
  }, [messages, busy])

  const send = async (text: string) => {
    const message = text.trim()
    if (!message || busy) return
    const history = messages.map(({ role, content }) => ({ role, content }))
    setMessages((m) => [...m, { role: 'user', content: message }])
    setInput('')
    setBusy(true)
    setError(null)
    try {
      const r = await api<ChatReply>('/insights/chat', { method: 'POST', body: JSON.stringify({ message, history }) })
      setMessages((m) => [...m, { role: 'assistant', content: r.answer, source: r.source, tools: r.tool_calls.map((t) => t.name) }])
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  const submit = (e: FormEvent) => {
    e.preventDefault()
    void send(input)
  }

  return (
    <Card title="Ask about your spending" className="flex flex-col">
      <div className="mb-4 max-h-[28rem] min-h-48 space-y-3 overflow-y-auto" aria-live="polite">
        {messages.length === 0 && (
          <div className="flex flex-wrap gap-2">
            {SUGGESTIONS.map((s) => (
              <button key={s} type="button" onClick={() => send(s)} className="min-h-10 cursor-pointer rounded-full border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-100 dark:border-slate-700 dark:hover:bg-slate-800">
                {s}
              </button>
            ))}
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-[85%] rounded-xl px-4 py-2 text-sm ${m.role === 'user' ? 'bg-indigo-600 text-white' : 'bg-slate-100 dark:bg-slate-800'}`}>
              <p className="whitespace-pre-wrap">{m.content}</p>
              {m.role === 'assistant' && (
                <p className="mt-1 text-xs text-slate-600 dark:text-slate-400">
                  {m.source === 'ai' ? 'Local AI' : 'Rule-based'}
                  {m.tools && m.tools.length > 0 ? ` · looked up: ${Array.from(new Set(m.tools)).join(', ')}` : ''}
                </p>
              )}
            </div>
          </div>
        ))}
        {busy && <Spinner label="Thinking" />}
        <div ref={endRef} />
      </div>
      <ErrorBox error={error} />
      <form onSubmit={submit} className="mt-2 flex gap-2">
        <label htmlFor="chat-input" className="sr-only">Your question</label>
        <input id="chat-input" className={inputCls} placeholder="e.g. How much did I spend on dining last month?" value={input} onChange={(e) => setInput(e.target.value)} maxLength={2000} />
        <Button type="submit" aria-label="Send question" disabled={!input.trim()} loading={busy}>
          <Send className="h-4 w-4" aria-hidden="true" />
        </Button>
      </form>
    </Card>
  )
}

export default function Insights() {
  const [month, setMonth] = useState(currentMonth())
  const [refreshKey, setRefreshKey] = useState(0)
  const params = qs({ month, refresh: refreshKey ? 'true' : undefined })
  const q = useQuery({ queryKey: ['insights', month, refreshKey], queryFn: () => api<InsightsT>(`/insights${params}`) })

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <h1 className="text-2xl font-semibold">AI insights</h1>
        <div className="flex items-end gap-2">
          <div className="flex flex-col gap-1">
            <label htmlFor="i-month" className="text-xs font-medium text-slate-700 dark:text-slate-300">Month</label>
            <input id="i-month" type="month" className={inputCls} value={month} onChange={(e) => e.target.value && setMonth(e.target.value)} />
          </div>
          <Button variant="secondary" onClick={() => setRefreshKey((k) => k + 1)} loading={q.isFetching}>
            <RefreshCw className="h-4 w-4" aria-hidden="true" /> Regenerate
          </Button>
        </div>
      </div>
      <div className="grid gap-6 lg:grid-cols-2">
        <Card title={`Summary for ${month}`} action={q.data && <Badge tone={q.data.source === 'ai' ? 'indigo' : 'slate'}>{q.data.source === 'ai' ? 'Local AI' : 'Rule-based'}</Badge>}>
          {q.isLoading && <Spinner label="Analysing your spending" />}
          <ErrorBox error={q.error} />
          {q.data && (
            <div className="space-y-5">
              <p className="leading-relaxed">{q.data.summary}</p>
              {q.data.anomalies.length > 0 && (
                <div>
                  <h3 className="mb-2 text-sm font-semibold">Worth a look</h3>
                  <ul className="space-y-2">
                    {q.data.anomalies.map((a, i) => (
                      <li key={i} className="flex gap-2 text-sm text-amber-900 dark:text-amber-200">
                        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
                        {a}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              <div>
                <h3 className="mb-2 text-sm font-semibold">Tips</h3>
                {q.data.tips.length === 0 ? (
                  <Empty>No tips yet.</Empty>
                ) : (
                  <ul className="space-y-2">
                    {q.data.tips.map((t, i) => (
                      <li key={i} className="flex gap-2 text-sm">
                        <Lightbulb className="mt-0.5 h-4 w-4 shrink-0 text-indigo-600 dark:text-indigo-400" aria-hidden="true" />
                        {t}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          )}
        </Card>
        <Chat />
      </div>
    </div>
  )
}
