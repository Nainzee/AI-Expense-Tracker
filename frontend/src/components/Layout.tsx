import { useQuery } from '@tanstack/react-query'
import { LayoutDashboard, LogOut, Moon, PiggyBank, ReceiptText, Sparkles, Sun, Upload, Wallet } from 'lucide-react'
import { useState } from 'react'
import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { api, clearToken, type Health } from '../lib/api'
import { Badge } from './ui'

const nav = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard, end: true },
  { to: '/upload', label: 'Scan receipt', icon: Upload },
  { to: '/receipts', label: 'Receipts', icon: ReceiptText },
  { to: '/budgets', label: 'Budgets', icon: PiggyBank },
  { to: '/insights', label: 'AI insights', icon: Sparkles },
]

function useTheme() {
  const [dark, setDark] = useState(() => document.documentElement.classList.contains('dark'))
  const toggle = () => {
    const next = !dark
    document.documentElement.classList.toggle('dark', next)
    localStorage.setItem('theme', next ? 'dark' : 'light')
    setDark(next)
  }
  return { dark, toggle }
}

export default function Layout() {
  const { dark, toggle } = useTheme()
  const navigate = useNavigate()
  const health = useQuery({ queryKey: ['health'], queryFn: () => api<Health>('/health'), refetchInterval: 30000 })
  const aiOn = health.data?.ollama.available

  const logout = () => {
    clearToken()
    navigate('/login')
  }

  return (
    <div className="min-h-screen md:flex">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-white focus:px-3 focus:py-2 focus:text-slate-900">
        Skip to content
      </a>
      <aside className="border-b border-slate-200 bg-white md:sticky md:top-0 md:h-screen md:w-60 md:shrink-0 md:border-b-0 md:border-r dark:border-slate-800 dark:bg-slate-900">
        <div className="flex items-center gap-2 px-5 py-4">
          <Wallet className="h-6 w-6 text-indigo-600 dark:text-indigo-400" aria-hidden="true" />
          <span className="text-lg font-semibold">Smart Expense</span>
        </div>
        <nav aria-label="Main" className="flex gap-1 overflow-x-auto px-3 pb-3 md:flex-col md:overflow-visible">
          {nav.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                `flex min-h-10 items-center gap-3 whitespace-nowrap rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
                  isActive
                    ? 'bg-indigo-50 text-indigo-700 dark:bg-indigo-950 dark:text-indigo-200'
                    : 'text-slate-700 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800'
                }`
              }
            >
              <Icon className="h-4 w-4" aria-hidden="true" />
              {label}
            </NavLink>
          ))}
        </nav>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-end gap-2 border-b border-slate-200 bg-white/80 px-4 py-3 backdrop-blur dark:border-slate-800 dark:bg-slate-900/80">
          {health.data && (
            <span title={aiOn ? `Models: ${health.data.ollama.models.join(', ')}` : 'Start Ollama to enable AI extraction and insights'}>
              <Badge tone={aiOn ? 'green' : 'amber'}>{aiOn ? 'Local AI on' : 'Local AI off · OCR only'}</Badge>
            </span>
          )}
          <button
            type="button"
            onClick={toggle}
            aria-label={dark ? 'Switch to light mode' : 'Switch to dark mode'}
            className="inline-flex h-10 w-10 cursor-pointer items-center justify-center rounded-lg text-slate-700 hover:bg-slate-100 dark:text-slate-200 dark:hover:bg-slate-800"
          >
            {dark ? <Sun className="h-5 w-5" aria-hidden="true" /> : <Moon className="h-5 w-5" aria-hidden="true" />}
          </button>
          {health.data?.auth_enabled !== false && (
            <button
              type="button"
              onClick={logout}
              aria-label="Log out"
              className="inline-flex h-10 w-10 cursor-pointer items-center justify-center rounded-lg text-slate-700 hover:bg-slate-100 dark:text-slate-200 dark:hover:bg-slate-800"
            >
              <LogOut className="h-5 w-5" aria-hidden="true" />
            </button>
          )}
        </header>
        <main id="main" className="mx-auto w-full max-w-7xl flex-1 p-4 md:p-6">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
