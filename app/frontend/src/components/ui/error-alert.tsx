import { AlertTriangle } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Card, CardContent } from "@/components/ui/card"

export function ErrorAlert({ error }: { error: string }) {
  if (!error) return null
  return (
    <Card className="border-destructive/20 bg-destructive/5 shadow-none">
      <CardContent className="flex items-start gap-3 p-4">
        <Badge variant="danger" className="shrink-0 gap-1.5">
          <AlertTriangle className="h-3.5 w-3.5" />
          Error
        </Badge>
        <div className="text-sm text-destructive">{error}</div>
      </CardContent>
    </Card>
  )
}
