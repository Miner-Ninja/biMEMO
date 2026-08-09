import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { AdminLayout } from "@/components/layout/AdminLayout";
import { MemoryDebugSectionNav } from "@/components/admin/memory-debug/MemoryDebugSectionNav";
import { MemoryDiagnosticsSection } from "@/components/admin/memory-debug/MemoryDiagnosticsSection";
import { MemoryDataSection } from "@/components/admin/memory-debug/MemoryDataSection";
import { MemoryOverviewSection } from "@/components/admin/memory-debug/MemoryOverviewSection";
import { MemoryProcessingLogsSection } from "@/components/admin/memory-debug/MemoryProcessingLogsSection";
import { AssertionsViewPanel } from "@/components/admin/memory-debug/AssertionsViewPanel";
import { MEMORY_DEBUG_SECTIONS } from "@/components/admin/memory-debug/constants";
import type { MemoryDebugSection } from "@/components/admin/memory-debug/types";
import { Button } from "@/components/ui/button";

export function MemoryDebugPage() {
  const { t } = useTranslation();
  const [activeSection, setActiveSection] = useState<MemoryDebugSection>("overview");
  const [refreshToken, setRefreshToken] = useState(0);

  const manualRefreshLabel = useMemo(() => {
    // Re-snapshot the time on each manual refresh (refreshToken bump).
    void refreshToken;
    return t("memoryDebug.manualRefreshAt", {
      time: new Date().toLocaleTimeString(),
    });
  }, [refreshToken, t]);

  const activeDescriptionKey = MEMORY_DEBUG_SECTIONS.find(
    (section) => section.id === activeSection
  )?.descriptionKey;

  const refreshPage = (): void => {
    setRefreshToken((previous) => previous + 1);
  };

  return (
    <AdminLayout title={t("memoryDebug.pageTitle")}>
      <div className="flex flex-1 flex-col gap-4 overflow-y-auto p-4 sm:p-6">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <MemoryDebugSectionNav
            activeSection={activeSection}
            onSectionChange={setActiveSection}
          />
          <div className="flex shrink-0 items-center gap-2">
            <div className="text-xs text-muted-foreground">{manualRefreshLabel}</div>
            <Button variant="outline" size="sm" onClick={refreshPage}>
              {t("memoryDebug.refresh")}
            </Button>
          </div>
        </div>
        {activeDescriptionKey && (
          <p className="font-prose text-sm text-muted-foreground">
            {t(activeDescriptionKey)}
          </p>
        )}

        {activeSection === "overview" ? (
          <MemoryOverviewSection refreshToken={refreshToken} />
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

        {activeSection === "assertions" ? (
          <AssertionsViewPanel refreshToken={refreshToken} />
        ) : null}
      </div>
    </AdminLayout>
  );
}
