import React, { useCallback, useEffect, useRef, useState } from 'react'
import { X } from 'lucide-react'
import { Badge } from './Display'
import styles from './Toast.module.css'
import { ToastContext, type ToastInput } from './toastContext'

interface ToastItem extends Required<Omit<ToastInput, 'description'>> {
  id: number
  description?: string
}

const TONE_LABEL: Record<ToastItem['tone'], string> = {
  neutral: 'Note',
  success: 'Done',
  warning: 'Warning',
  danger: 'Error',
  info: 'Info',
}

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([])
  const nextId = useRef(1)
  const timers = useRef(new Map<number, ReturnType<typeof setTimeout>>())

  const dismiss = useCallback((id: number) => {
    setToasts((current) => current.filter((toast) => toast.id !== id))
    const timer = timers.current.get(id)
    if (timer) clearTimeout(timer)
    timers.current.delete(id)
  }, [])

  const push = useCallback(
    ({ title, description, tone = 'neutral', duration = 5000 }: ToastInput) => {
      const id = nextId.current++
      setToasts((current) => [...current.slice(-3), { id, title, description, tone, duration }])
      if (duration > 0) timers.current.set(id, setTimeout(() => dismiss(id), duration))
    },
    [dismiss],
  )

  useEffect(() => {
    const pending = timers.current
    return () => pending.forEach(clearTimeout)
  }, [])

  return (
    <ToastContext.Provider value={push}>
      {children}
      {/* One persistent live region: content added to it is announced. */}
      <ol className={styles.region} aria-live="polite" aria-label="Notifications">
        {toasts.map((toast) => (
          <li key={toast.id} className={styles.toast}>
            <div className={styles.content}>
              <Badge tone={toast.tone} icon>{TONE_LABEL[toast.tone]}</Badge>
              <p className={styles.title}>{toast.title}</p>
              {toast.description && <p className={styles.description}>{toast.description}</p>}
            </div>
            <button type="button" className={styles.close} onClick={() => dismiss(toast.id)} aria-label="Dismiss notification">
              <X size={16} aria-hidden="true" />
            </button>
          </li>
        ))}
      </ol>
    </ToastContext.Provider>
  )
}
