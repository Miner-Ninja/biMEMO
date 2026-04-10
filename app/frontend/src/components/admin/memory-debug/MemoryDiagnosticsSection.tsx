import { useState } from "react"

import { DIAGNOSTICS_VIEWS } from "@/components/admin/memory-debug/constants"
import { AuditDiagnosticsPanel } from "@/components/admin/memory-debug/AuditDiagnosticsPanel"
import { QualityDiagnosticsPanel } from "@/components/admin/memory-debug/QualityDiagnosticsPanel"
import { RoutingDiagnosticsPanel } from "@/components/admin/memory-debug/RoutingDiagnosticsPanel"
import { SubsectionNav } from "@/components/admin/memory-debug/SubsectionNav"
import type { DiagnosticsView } from "@/components/admin/memory-debug/types"

export function MemoryDiagnosticsSection({ refreshToken }: { refreshToken: number }) {
  const [activeView, setActiveView] = useState<DiagnosticsView>("routing")

  return (
    <div className="space-y-6">
      <SubsectionNav items={DIAGNOSTICS_VIEWS} activeItem={activeView} onChange={setActiveView} />

      {activeView === "routing" ? <RoutingDiagnosticsPanel refreshToken={refreshToken} /> : null}
      {activeView === "quality" ? <QualityDiagnosticsPanel refreshToken={refreshToken} /> : null}
      {activeView === "audit" ? <AuditDiagnosticsPanel refreshToken={refreshToken} /> : null}
    </div>
  )
}
