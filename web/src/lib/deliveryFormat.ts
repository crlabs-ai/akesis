import type { WebhookDelivery } from '../types/api'

export const formatTime = (iso: string | null): string => {
  if (!iso) return '—'
  const parsed = new Date(iso)
  return Number.isNaN(parsed.getTime()) ? iso : parsed.toLocaleString()
}

/** Describes when a scheduled delivery becomes due, without implying that anything runs it now. */
export const describeNextRetry = (delivery: WebhookDelivery, now: number): string => {
  if (delivery.status === 'exhausted') return 'No further attempts'
  if (!delivery.next_retry_at) return 'Not scheduled'
  const due = new Date(delivery.next_retry_at).getTime()
  if (Number.isNaN(due)) return formatTime(delivery.next_retry_at)
  if (due <= now) return 'Due. The retry worker claims it on its next pass.'
  const minutes = Math.ceil((due - now) / 60_000)
  return `In about ${minutes} min (${formatTime(delivery.next_retry_at)})`
}

