import { useState } from "react"

import { PROCESSING_LOG_VIEWS } from "@/components/admin/memory-debug/constants"
import { RecordsLogPanel } from "@/components/admin/memory-debug/RecordsLogPanel"
import { SubsectionNav } from "@/components/admin/memory-debug/SubsectionNav"
import { SummariesLogPanel } from "@/components/admin/memory-debug/SummariesLogPanel"
import type { ProcessingLogsView } from "@/components/admin/memory-debug/types"

export function MemoryProcessingLogsSection({ refreshToken }: { refreshToken: number }) {
  const [activeView, setActiveView] = useState<ProcessingLogsView>("records-log")

  return (
    <div className="space-y-6">
      <SubsectionNav items={PROCESSING_LOG_VIEWS} activeItem={activeView} onChange={setActiveView} />

      {activeView === "records-log" ? <RecordsLogPanel refreshToken={refreshToken} /> : null}
      {activeView === "summaries-log" ? <SummariesLogPanel refreshToken={refreshToken} /> : null}
    </div>
  )
}
