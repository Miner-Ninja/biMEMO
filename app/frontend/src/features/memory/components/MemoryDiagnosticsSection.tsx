import { useState } from "react";

import { DIAGNOSTICS_VIEWS } from "@/features/memory/components/constants";
import { AuditDiagnosticsPanel } from "@/features/memory/components/AuditDiagnosticsPanel";
import { QualityDiagnosticsPanel } from "@/features/memory/components/QualityDiagnosticsPanel";
import { RoutingDiagnosticsPanel } from "@/features/memory/components/RoutingDiagnosticsPanel";
import { SubsectionNav } from "@/features/memory/components/SubsectionNav";
import type { DiagnosticsView } from "@/features/memory/components/types";

export function MemoryDiagnosticsSection({ refreshToken }: { refreshToken: number }) {
  const [activeView, setActiveView] = useState<DiagnosticsView>("routing");

  return (
    <div className="space-y-6">
      <SubsectionNav
        items={DIAGNOSTICS_VIEWS}
        activeItem={activeView}
        onChange={setActiveView}
      />

      {activeView === "routing" ? (
        <RoutingDiagnosticsPanel refreshToken={refreshToken} />
      ) : null}
      {activeView === "quality" ? (
        <QualityDiagnosticsPanel refreshToken={refreshToken} />
      ) : null}
      {activeView === "audit" ? (
        <AuditDiagnosticsPanel refreshToken={refreshToken} />
      ) : null}
    </div>
  );
}
