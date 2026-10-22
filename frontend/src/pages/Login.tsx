import { LogIn, UserPlus, Wallet } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button, ErrorBox, Field, inputCls } from '../components/ui'
import { api, setToken, setUser } from '../lib/api'

type AuthMode = 'login' | 'signup'

interface AuthResponse {
  access_token: string
  token_type: string
  username: string
}

export default function Login() {
  const navigate = useNavigate()
  const [mode, setMode] = useState<AuthMode>('login')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [error, setError] = useState<unknown>(null)
  const [loading, setLoading] = useState(false)

  const switchMode = (newMode: AuthMode) => {
    setMode(newMode)
    setError(null)
  }

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setError(null)

    const trimmedUser = username.trim()
    if (!trimmedUser) {
      setError(new Error('Please enter a username.'))
      return
    }

    if (mode === 'signup') {
      if (trimmedUser.length < 3) {
        setError(new Error('Username must be at least 3 characters long.'))
        return
      }
      if (password.length < 4) {
        setError(new Error('Password must be at least 4 characters long.'))
        return
      }
      if (password !== confirmPassword) {
        setError(new Error('Passwords do not match. Please re-enter.'))
        return
      }
    }

    setLoading(true)
    try {
      const endpoint = mode === 'signup' ? '/auth/signup' : '/auth/login'
      const res = await api<AuthResponse>(endpoint, {
        method: 'POST',
        body: JSON.stringify({ username: trimmedUser, password }),
      })

      setToken(res.access_token)
      setUser(res.username || trimmedUser)
      navigate('/', { replace: true })
    } catch (err) {
      setError(err)
    } finally {
      setLoading(false)
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center p-4 bg-slate-50 dark:bg-slate-950">
      <div className="w-full max-w-md rounded-2xl border border-slate-200 bg-white p-8 shadow-md dark:border-slate-800 dark:bg-slate-900">
        <div className="flex items-center gap-3 mb-2">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-indigo-600 text-white shadow-sm dark:bg-indigo-500">
            <Wallet className="h-6 w-6" aria-hidden="true" />
          </div>
          <div>
            <h1 className="text-xl font-bold tracking-tight text-slate-900 dark:text-slate-50">
              Smart Expense Tracker
            </h1>
            <p className="text-xs text-slate-500 dark:text-slate-400">
              Personal finance & intelligent receipt tracking
            </p>
          </div>
        </div>

        {/* Tab Switcher */}
        <div className="mt-6 mb-6 grid grid-cols-2 rounded-xl bg-slate-100 p-1 dark:bg-slate-800/70">
          <button
            type="button"
            onClick={() => switchMode('login')}
            className={`flex items-center justify-center gap-2 rounded-lg py-2 text-sm font-medium transition-all ${
              mode === 'login'
                ? 'bg-white text-slate-900 shadow-sm dark:bg-slate-900 dark:text-slate-100'
                : 'text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-100'
            }`}
          >
            <LogIn className="h-4 w-4" />
            Sign in
          </button>
          <button
            type="button"
            onClick={() => switchMode('signup')}
            className={`flex items-center justify-center gap-2 rounded-lg py-2 text-sm font-medium transition-all ${
              mode === 'signup'
                ? 'bg-white text-slate-900 shadow-sm dark:bg-slate-900 dark:text-slate-100'
                : 'text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-100'
            }`}
          >
            <UserPlus className="h-4 w-4" />
            Create account
          </button>
        </div>

        <form onSubmit={submit} className="space-y-4">
          <Field
            label="Username"
            htmlFor="username"
            hint={
              mode === 'signup' ? (
                <span className="text-xs text-slate-500">Letters, numbers, dashes (min 3 chars)</span>
              ) : undefined
            }
          >
            <input
              id="username"
              className={inputCls}
              autoComplete="username"
              placeholder={mode === 'signup' ? 'choose a username' : 'your username'}
              required
              value={username}
              onChange={(e) => setUsername(e.target.value)}
            />
          </Field>

          <Field
            label="Password"
            htmlFor="password"
            hint={
              mode === 'signup' ? (
                <span className="text-xs text-slate-500">Minimum 4 characters</span>
              ) : undefined
            }
          >
            <input
              id="password"
              type="password"
              className={inputCls}
              autoComplete={mode === 'signup' ? 'new-password' : 'current-password'}
              placeholder="••••••••"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </Field>

          {mode === 'signup' && (
            <Field label="Confirm Password" htmlFor="confirmPassword">
              <input
                id="confirmPassword"
                type="password"
                className={inputCls}
                autoComplete="new-password"
                placeholder="re-type password"
                required
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
              />
            </Field>
          )}

          <ErrorBox error={error} />

          <Button type="submit" loading={loading} className="w-full mt-2">
            {mode === 'login' ? 'Sign in' : 'Create account'}
          </Button>

          <div className="pt-2 text-center text-xs text-slate-500 dark:text-slate-400">
            {mode === 'login' ? (
              <p>
                Don't have an account?{' '}
                <button
                  type="button"
                  onClick={() => switchMode('signup')}
                  className="font-medium text-indigo-600 hover:underline dark:text-indigo-400"
                >
                  Create one now
                </button>
              </p>
            ) : (
              <p>
                Already have an account?{' '}
                <button
                  type="button"
                  onClick={() => switchMode('login')}
                  className="font-medium text-indigo-600 hover:underline dark:text-indigo-400"
                >
                  Sign in instead
                </button>
              </p>
            )}
          </div>
        </form>
      </div>
    </main>
  )
}
