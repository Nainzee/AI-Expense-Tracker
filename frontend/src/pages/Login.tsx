import { Wallet } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button, ErrorBox, Field, inputCls } from '../components/ui'
import { api, setToken } from '../lib/api'

export default function Login() {
  const navigate = useNavigate()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<unknown>(null)
  const [loading, setLoading] = useState(false)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setError(null)
    setLoading(true)
    try {
      const res = await api<{ access_token: string }>('/auth/login', {
        method: 'POST',
        body: JSON.stringify({ username, password }),
      })
      setToken(res.access_token)
      navigate('/', { replace: true })
    } catch (err) {
      setError(err)
    } finally {
      setLoading(false)
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center p-4">
      <form onSubmit={submit} className="w-full max-w-sm space-y-5 rounded-xl border border-slate-200 bg-white p-8 shadow-sm dark:border-slate-800 dark:bg-slate-900">
        <div className="flex items-center gap-2">
          <Wallet className="h-7 w-7 text-indigo-600 dark:text-indigo-400" aria-hidden="true" />
          <h1 className="text-xl font-semibold">Smart Expense Tracker</h1>
        </div>
        <p className="text-sm text-slate-600 dark:text-slate-400">Sign in to scan receipts and see your spending.</p>
        <Field label="Username" htmlFor="username">
          <input id="username" className={inputCls} autoComplete="username" required value={username} onChange={(e) => setUsername(e.target.value)} />
        </Field>
        <Field label="Password" htmlFor="password">
          <input id="password" type="password" className={inputCls} autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
        <ErrorBox error={error} />
        <Button type="submit" loading={loading} className="w-full">
          Sign in
        </Button>
      </form>
    </main>
  )
}
