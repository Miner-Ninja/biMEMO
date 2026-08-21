import { useState } from "react";

import { PROCESSING_LOG_VIEWS } from "@/features/memory/components/constants";
import { RecordsLogPanel } from "@/features/memory/components/RecordsLogPanel";
import { SubsectionNav } from "@/features/memory/components/SubsectionNav";
import type { ProcessingLogsView } from "@/features/memory/components/types";

export function MemoryProcessingLogsSection({
  refreshToken,
}: {
  refreshToken: number;
}) {
  const [activeView, setActiveView] = useState<ProcessingLogsView>("records-log");

  return (
    <div className="space-y-6">
      <SubsectionNav
        items={PROCESSING_LOG_VIEWS}
        activeItem={activeView}
        onChange={setActiveView}
      />

      {activeView === "records-log" ? (
        <RecordsLogPanel refreshToken={refreshToken} />
      ) : null}
    </div>
  );
}
