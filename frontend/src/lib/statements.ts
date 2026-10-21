import { useQuery } from '@tanstack/react-query'
import { api, type ReviewQueue, type Transaction } from './api'

export function useReviewQueue() {
  return useQuery({ queryKey: ['txn-review'], queryFn: () => api<ReviewQueue>('/transactions/review') })
}

export function payeeName(t: Pick<Transaction, 'merchant' | 'description'> & { counterparty?: string | null }) {
  return t.merchant || t.counterparty || t.description.slice(0, 30)
}
