import { Check, X, Loader2 } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent } from '@/components/ui/card'
import { Separator } from '@/components/ui/separator'

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
  const title = isComplete
    ? `Uploaded ${progress.total} file${progress.total > 1 ? 's' : ''}`
    : `Uploading ${progress.current} of ${progress.total}`

  return (
    <Card className="shadow-none">
      <CardContent className="flex flex-col gap-3 p-4">
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0">
            <p className="text-sm font-medium">{title}</p>
            <p className="text-xs text-muted-foreground">
              {isComplete ? 'Upload queue finished' : `Current file: ${progress.filename}`}
            </p>
          </div>
          <Badge variant={isComplete ? 'success' : 'info'}>{percent}%</Badge>
        </div>

        <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
          <div
            className="h-full rounded-full bg-primary transition-all duration-300"
            style={{ width: `${percent}%` }}
          />
        </div>

        {progress.results.length > 0 ? <Separator /> : null}

        <div className="flex flex-col gap-1.5">
          {progress.results.map((result, index) => (
            <div key={`${result.name}-${index}`} className="flex items-center gap-2 text-xs">
              <Badge variant={result.ok ? 'success' : 'danger'} className="shrink-0 gap-1 border-transparent px-1.5 py-0">
                {result.ok ? <Check className="h-3 w-3" /> : <X className="h-3 w-3" />}
              </Badge>
              <span className="truncate">{result.name}</span>
            </div>
          ))}

          {!isComplete && (
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" />
              <span className="truncate">{progress.filename}</span>
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  )
}
