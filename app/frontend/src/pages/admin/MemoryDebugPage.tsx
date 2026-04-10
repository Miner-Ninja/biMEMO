import { useMemo, useState } from "react"

import { AdminLayout } from "@/components/layout/AdminLayout"
import { MemoryDebugSectionNav } from "@/components/admin/memory-debug/MemoryDebugSectionNav"
import { MemoryDiagnosticsSection } from "@/components/admin/memory-debug/MemoryDiagnosticsSection"
import { MemoryDataSection } from "@/components/admin/memory-debug/MemoryDataSection"
import { MemoryOverviewSection } from "@/components/admin/memory-debug/MemoryOverviewSection"
import { MemoryProcessingLogsSection } from "@/components/admin/memory-debug/MemoryProcessingLogsSection"
import { SummariesViewPanel } from "@/components/admin/memory-debug/SummariesViewPanel"
import type { MemoryDebugSection } from "@/components/admin/memory-debug/types"
import { Button } from "@/components/ui/button"

export function MemoryDebugPage() {
  const [activeSection, setActiveSection] = useState<MemoryDebugSection>("overview")
  const [refreshToken, setRefreshToken] = useState(0)

  const lastUpdatedLabel = useMemo(() => {
    return `Last refreshed at ${new Date().toLocaleTimeString()}`
  }, [refreshToken])

  const refreshPage = (): void => {
    setRefreshToken((previous) => previous + 1)
  }

  return (
    <AdminLayout title="Memory Debug">
    <div className="flex flex-1 flex-col gap-6 overflow-y-auto p-4 sm:p-6">
      <div className="flex items-center justify-between">
        <p className="max-w-3xl text-sm text-muted-foreground">
          Review memory health, inspect stored data, and trace how messages become records and summaries.
        </p>
        <div className="flex items-center gap-2 shrink-0">
          <div className="text-xs text-muted-foreground">{lastUpdatedLabel}</div>
          <Button variant="outline" size="sm" onClick={refreshPage}>
            Refresh
          </Button>
        </div>
      </div>
      <MemoryDebugSectionNav activeSection={activeSection} onSectionChange={setActiveSection} />

      {activeSection === "overview" ? (
        <MemoryOverviewSection
          refreshToken={refreshToken}
        />
      ) : null}

      {activeSection === "memory-data" ? (
        <MemoryDataSection refreshToken={refreshToken} />
      ) : null}

      {activeSection === "diagnostics" ? (
        <MemoryDiagnosticsSection refreshToken={refreshToken} />
      ) : null}

      {activeSection === "processing-logs" ? (
        <MemoryProcessingLogsSection refreshToken={refreshToken} />
      ) : null}

      {activeSection === "summaries" ? (
        <SummariesViewPanel refreshToken={refreshToken} />
      ) : null}
    </div>
    </AdminLayout>
  )
}
