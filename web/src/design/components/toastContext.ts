import { createContext, useContext } from 'react'
import type { Tone } from './Display'

export interface ToastInput {
  title: string
  description?: string
  tone?: Extract<Tone, 'neutral' | 'success' | 'warning' | 'danger' | 'info'>
  /** Milliseconds before auto-dismiss; 0 keeps it until dismissed. */
  duration?: number
}

export const ToastContext = createContext<((toast: ToastInput) => void) | null>(null)

/** Push a toast. Must be called inside <ToastProvider>. */
export function useToast(): (toast: ToastInput) => void {
  const push = useContext(ToastContext)
  if (!push) throw new Error('useToast must be used inside <ToastProvider>')
  return push
}
