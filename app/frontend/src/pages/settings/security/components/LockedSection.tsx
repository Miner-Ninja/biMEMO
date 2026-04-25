/**
 * Always-On lock list shown at the bottom of the security page.
 *
 * Renders every catalog entry with `locked=true` as an
 * informational card with a Lock icon and a tooltip carrying
 * the description. No interactive controls.
 */

import { Lock } from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import type { SecuritySetting } from "@/api/security"
import { PAGE_COPY } from "../i18n/ru"

interface LockedSectionProps {
  settings: SecuritySetting[]
}

export function LockedSection({ settings }: LockedSectionProps) {
  if (settings.length === 0) return null
  return (
    <section
      aria-label={PAGE_COPY.alwaysOnSection}
      className="flex flex-col gap-2"
    >
      <h2 className="text-sm font-medium text-muted-foreground">
        {PAGE_COPY.alwaysOnSection}
      </h2>
      <TooltipProvider delayDuration={200}>
        <div className="flex flex-col gap-1.5">
          {settings.map((entry) => (
            <Tooltip key={entry.key}>
              <TooltipTrigger asChild>
                <Card className="bg-muted/40">
                  <CardContent className="flex items-center gap-3 p-3">
                    <Lock className="size-4 text-muted-foreground shrink-0" />
                    <span className="text-sm font-medium">
                      {entry.label}
                    </span>
                  </CardContent>
                </Card>
              </TooltipTrigger>
              <TooltipContent side="left" className="max-w-sm">
                {entry.description}
              </TooltipContent>
            </Tooltip>
          ))}
        </div>
      </TooltipProvider>
    </section>
  )
}
