import { useState } from "react";

import { MEMORY_DATA_VIEWS } from "@/features/memory/components/constants";
import { ContextSnapshotPanel } from "@/features/memory/components/ContextSnapshotPanel";
import { FileContextPanel } from "@/features/memory/components/FileContextPanel";
import { MemoryLinksPanel } from "@/features/memory/components/MemoryLinksPanel";
import { MemoryTimelinePanel } from "@/features/memory/components/MemoryTimelinePanel";
import { RecordQueuePanel } from "@/features/memory/components/RecordQueuePanel";
import { EntitiesBrowsePanel } from "@/features/memory/components/EntitiesBrowsePanel";
import { SubsectionNav } from "@/features/memory/components/SubsectionNav";
import type { MemoryDataView } from "@/features/memory/components/types";

export function MemoryDataSection({ refreshToken }: { refreshToken: number }) {
  const [activeView, setActiveView] = useState<MemoryDataView>("context");

  return (
    <div className="space-y-6">
      <SubsectionNav
        items={MEMORY_DATA_VIEWS}
        activeItem={activeView}
        onChange={setActiveView}
      />

      {activeView === "context" ? (
        <ContextSnapshotPanel refreshToken={refreshToken} />
      ) : null}
      {activeView === "timeline" ? (
        <MemoryTimelinePanel refreshToken={refreshToken} />
      ) : null}
      {activeView === "links" ? <MemoryLinksPanel refreshToken={refreshToken} /> : null}
      {activeView === "files" ? <FileContextPanel refreshToken={refreshToken} /> : null}
      {activeView === "queue" ? <RecordQueuePanel refreshToken={refreshToken} /> : null}
      {activeView === "entities" ? (
        <EntitiesBrowsePanel refreshToken={refreshToken} />
      ) : null}
    </div>
  );
}
