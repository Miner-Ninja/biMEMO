import { useEffect, useState, useCallback } from 'react'
import { CheckCircle2, XCircle, X } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
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
  const statusLabel = toast.type === 'success' ? 'Success' : 'Error'

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
    <Card
      className={cn(
        'shadow-lg transition-all duration-200',
        visible ? 'translate-x-0 opacity-100' : 'translate-x-4 opacity-0',
        toast.type === 'success'
          ? 'border-success/20 bg-card'
          : 'border-destructive/20 bg-card'
      )}
    >
      <CardContent className="flex items-start gap-3 p-4">
        <Badge variant={toast.type === 'success' ? 'success' : 'danger'} className="shrink-0 gap-1.5">
          {toast.type === 'success' ? (
            <CheckCircle2 className="h-3.5 w-3.5" />
          ) : (
            <XCircle className="h-3.5 w-3.5" />
          )}
          {statusLabel}
        </Badge>
        <span className="flex-1 text-sm">{toast.message}</span>
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          className="shrink-0"
          onClick={() => {
            setVisible(false)
            setTimeout(() => onDismiss(toast.id), 200)
          }}
          aria-label="Dismiss toast"
        >
          <X />
        </Button>
      </CardContent>
    </Card>
  )
}

interface ToastContainerProps {
  toasts: Toast[]
  onDismiss: (id: string) => void
}

export function ToastContainer({ toasts, onDismiss }: ToastContainerProps) {
  if (toasts.length === 0) return null

  return (
    <div className="fixed right-4 top-4 z-50 flex w-80 flex-col gap-2">
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
