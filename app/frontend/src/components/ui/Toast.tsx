import { useEffect, useState, useCallback } from 'react'
import { CheckCircle2, XCircle, X } from 'lucide-react'
import { cn, generateId } from '@/lib/utils'

export interface Toast {
  id: string
  type: 'success' | 'error'
  message: string
  duration?: number
}

interface ToastItemProps {
  toast: Toast
  onDismiss: (id: string) => void
}

function ToastItem({ toast, onDismiss }: ToastItemProps) {
  const [visible, setVisible] = useState(false)

  useEffect(() => {
    // Animate in
    requestAnimationFrame(() => setVisible(true))

    const timer = setTimeout(() => {
      setVisible(false)
      setTimeout(() => onDismiss(toast.id), 200)
    }, toast.duration || 5000)

    return () => clearTimeout(timer)
  }, [toast, onDismiss])

  return (
    <div
      className={cn(
        'flex items-center gap-2 rounded-lg border px-4 py-3 shadow-lg transition-all duration-200',
        visible ? 'translate-x-0 opacity-100' : 'translate-x-4 opacity-0',
        toast.type === 'success'
          ? 'border-green-500/20 bg-background text-foreground'
          : 'border-destructive/20 bg-background text-foreground'
      )}
    >
      {toast.type === 'success' ? (
        <CheckCircle2 className="h-4 w-4 shrink-0 text-green-500" />
      ) : (
        <XCircle className="h-4 w-4 shrink-0 text-destructive" />
      )}
      <span className="text-sm flex-1">{toast.message}</span>
      <button
        type="button"
        className="shrink-0 text-muted-foreground hover:text-foreground"
        onClick={() => {
          setVisible(false)
          setTimeout(() => onDismiss(toast.id), 200)
        }}
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  )
}

interface ToastContainerProps {
  toasts: Toast[]
  onDismiss: (id: string) => void
}

export function ToastContainer({ toasts, onDismiss }: ToastContainerProps) {
  if (toasts.length === 0) return null

  return (
    <div className="fixed top-4 right-4 z-50 flex flex-col gap-2 w-80">
      {toasts.slice(0, 3).map((toast) => (
        <ToastItem key={toast.id} toast={toast} onDismiss={onDismiss} />
      ))}
    </div>
  )
}

/**
 * Hook for managing toast state.
 */
export function useToasts() {
  const [toasts, setToasts] = useState<Toast[]>([])

  const addToast = useCallback((toast: Omit<Toast, 'id'>) => {
    const id = generateId()
    setToasts((prev) => [...prev, { ...toast, id }])
  }, [])

  const dismissToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id))
  }, [])

  return { toasts, addToast, dismissToast }
}
