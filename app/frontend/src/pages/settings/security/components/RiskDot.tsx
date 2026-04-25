/**
 * Coloured dot communicating the risk level of one setting.
 *
 * Uses semantic theme tokens (--color-risk-low/medium/high) and
 * a muted gray for the locked state. Critical-level settings are
 * surfaced in `LockedSection` rather than as editable cards, so
 * this component never renders the "critical" colour itself.
 */

import { cn } from "@/lib/utils"
import { RISK_LABEL } from "../i18n/ru"
import type { SecurityRiskLevel } from "@/api/security"

interface RiskDotProps {
  level: SecurityRiskLevel
  className?: string
}

const RISK_CLASS: Record<SecurityRiskLevel, string> = {
  low: "bg-risk-low",
  medium: "bg-risk-medium",
  high: "bg-risk-high",
  critical: "bg-muted-foreground",
}

export function RiskDot({ level, className }: RiskDotProps) {
  return (
    <span
      role="img"
      aria-label={RISK_LABEL[level]}
      title={RISK_LABEL[level]}
      className={cn(
        "inline-block size-2 shrink-0 rounded-full",
        RISK_CLASS[level],
        className,
      )}
    />
  )
}
