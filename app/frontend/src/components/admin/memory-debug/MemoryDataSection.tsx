import { useState } from "react"

import { MEMORY_DATA_VIEWS } from "@/components/admin/memory-debug/constants"
import { ContextSnapshotPanel } from "@/components/admin/memory-debug/ContextSnapshotPanel"
import { FileContextPanel } from "@/components/admin/memory-debug/FileContextPanel"
import { MemoryLinksPanel } from "@/components/admin/memory-debug/MemoryLinksPanel"
import { MemoryTimelinePanel } from "@/components/admin/memory-debug/MemoryTimelinePanel"
import { SubsectionNav } from "@/components/admin/memory-debug/SubsectionNav"
import type { MemoryDataView } from "@/components/admin/memory-debug/types"

export function MemoryDataSection({ refreshToken }: { refreshToken: number }) {
  const [activeView, setActiveView] = useState<MemoryDataView>("context")

  return (
    <div className="space-y-6">
      <SubsectionNav items={MEMORY_DATA_VIEWS} activeItem={activeView} onChange={setActiveView} />

      {activeView === "context" ? <ContextSnapshotPanel refreshToken={refreshToken} /> : null}
      {activeView === "timeline" ? <MemoryTimelinePanel refreshToken={refreshToken} /> : null}
      {activeView === "links" ? <MemoryLinksPanel refreshToken={refreshToken} /> : null}
      {activeView === "files" ? <FileContextPanel refreshToken={refreshToken} /> : null}
    </div>
  )
}
