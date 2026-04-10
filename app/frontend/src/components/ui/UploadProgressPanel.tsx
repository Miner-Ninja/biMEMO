import { Check, X, Loader2 } from 'lucide-react'

export interface UploadProgressData {
  current: number
  total: number
  filename: string
  results: Array<{ name: string; ok: boolean }>
}

interface UploadProgressPanelProps {
  progress: UploadProgressData
}

export function UploadProgressPanel({ progress }: UploadProgressPanelProps) {
  const isComplete = progress.results.length === progress.total
  const percent = Math.round((progress.results.length / progress.total) * 100)

  return (
    <div className="rounded-lg border bg-card p-3 text-sm">
      <p className="font-medium mb-2">
        {isComplete
          ? `Uploaded ${progress.total} file${progress.total > 1 ? 's' : ''}`
          : `Uploading ${progress.current} of ${progress.total}...`}
      </p>
      <div className="h-1.5 w-full rounded-full bg-muted mb-2">
        <div
          className="h-full rounded-full bg-primary transition-all duration-300"
          style={{ width: `${percent}%` }}
        />
      </div>
      <div className="space-y-1">
        {progress.results.map((r, i) => (
          <div key={i} className="flex items-center gap-1.5 text-xs">
            {r.ok
              ? <Check className="h-3.5 w-3.5 text-green-500 shrink-0" />
              : <X className="h-3.5 w-3.5 text-destructive shrink-0" />}
            <span className="truncate">{r.name}</span>
          </div>
        ))}
        {!isComplete && (
          <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 animate-spin shrink-0" />
            <span className="truncate">{progress.filename}</span>
          </div>
        )}
      </div>
    </div>
  )
}
