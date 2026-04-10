import { Database } from 'lucide-react'
import { AdminLayout } from '@/components/layout/AdminLayout'

export function BackupsPage() {
  return (
    <AdminLayout title="Backups">
    <div className="flex-1 overflow-y-auto p-6 max-w-3xl">
      <div className="rounded-xl border border-border bg-card p-6">
        <div className="flex items-start gap-4">
          <div className="rounded-lg bg-primary/10 p-3 text-primary">
            <Database className="h-5 w-5" />
          </div>
          <div className="space-y-3">
            <div>
              <h2 className="font-semibold">Not available in the current runtime</h2>
              <p className="mt-1 text-sm text-muted-foreground">
                The active multi-process FastAPI backend does not expose backup routes yet.
                This page is kept as a placeholder so the UI does not point to broken API calls.
              </p>
            </div>
            <p className="text-sm text-muted-foreground">
              If backup support is needed, it should be implemented against the active
              `backend.processes.fastapi_app` stack rather than the legacy route layer.
            </p>
          </div>
        </div>
      </div>
    </div>
    </AdminLayout>
  )
}
