import { NavLink } from 'react-router-dom'
import { useReviewQueue } from '../lib/statements'

const tabs = [
  { to: '/statements', label: 'Import', end: true },
  { to: '/statements/transactions', label: 'Transactions' },
  { to: '/statements/review', label: 'Needs review' },
]

export default function StatementTabs() {
  const count = useReviewQueue().data?.count ?? 0
  return (
    <nav aria-label="Statements" className="flex gap-1 overflow-x-auto border-b border-slate-200 dark:border-slate-800">
      {tabs.map((t) => (
        <NavLink
          key={t.to}
          to={t.to}
          end={t.end}
          className={({ isActive }) =>
            `-mb-px inline-flex min-h-11 items-center gap-2 whitespace-nowrap border-b-2 px-3 text-sm font-medium ${
              isActive
                ? 'border-indigo-600 text-indigo-700 dark:border-indigo-400 dark:text-indigo-300'
                : 'border-transparent text-slate-700 hover:text-slate-900 dark:text-slate-300 dark:hover:text-white'
            }`
          }
        >
          {t.label}
          {t.to === '/statements/review' && count > 0 && (
            <span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs text-amber-900 dark:bg-amber-900/60 dark:text-amber-200">
              {count}
              <span className="sr-only"> items</span>
            </span>
          )}
        </NavLink>
      ))}
    </nav>
  )
}
