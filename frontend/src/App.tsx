import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import Layout from './components/Layout'
import { getToken } from './lib/api'
import Budgets from './pages/Budgets'
import Dashboard from './pages/Dashboard'
import Insights from './pages/Insights'
import Login from './pages/Login'
import ReceiptDetail from './pages/ReceiptDetail'
import Receipts from './pages/Receipts'
import Splits from './pages/Splits'
import StatementReview from './pages/StatementReview'
import Statements from './pages/Statements'
import Transactions from './pages/Transactions'
import UploadPage from './pages/Upload'

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
})

function RequireAuth({ children }: { children: ReactNode }) {
  return getToken() ? <>{children}</> : <Navigate to="/login" replace />
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route
            element={
              <RequireAuth>
                <Layout />
              </RequireAuth>
            }
          >
            <Route index element={<Dashboard />} />
            <Route path="upload" element={<UploadPage />} />
            <Route path="receipts" element={<Receipts />} />
            <Route path="receipts/:id" element={<ReceiptDetail />} />
            <Route path="statements" element={<Statements />} />
            <Route path="statements/transactions" element={<Transactions />} />
            <Route path="statements/review" element={<StatementReview />} />
            <Route path="splits" element={<Splits />} />
            <Route path="budgets" element={<Budgets />} />
            <Route path="insights" element={<Insights />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
