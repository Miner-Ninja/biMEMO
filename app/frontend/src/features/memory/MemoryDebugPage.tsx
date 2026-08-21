import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { AdminLayout } from "@/components/layout/AdminLayout";
import { Button } from "@/components/ui/button";
import { MemoryStatusStrip } from "@/features/memory/components/MemoryStatusStrip";
import { MemoryOverviewSection } from "@/features/memory/components/MemoryOverviewSection";
import { MemoryTimelinePanel } from "@/features/memory/components/MemoryTimelinePanel";
import { AssertionsViewPanel } from "@/features/memory/components/AssertionsViewPanel";
import { EntitiesBrowsePanel } from "@/features/memory/components/EntitiesBrowsePanel";
import { FileContextPanel } from "@/features/memory/components/FileContextPanel";
import { RecordQueuePanel } from "@/features/memory/components/RecordQueuePanel";
import { MemoryLinksPanel } from "@/features/memory/components/MemoryLinksPanel";
import { RoutingDiagnosticsPanel } from "@/features/memory/components/RoutingDiagnosticsPanel";
import { ContextSnapshotPanel } from "@/features/memory/components/ContextSnapshotPanel";
import { ProblemsPanel } from "@/features/memory/components/ProblemsPanel";
import { QualityDiagnosticsPanel } from "@/features/memory/components/QualityDiagnosticsPanel";
import { AuditDiagnosticsPanel } from "@/features/memory/components/AuditDiagnosticsPanel";
import { ExtractorTracePanel } from "@/features/memory/components/ExtractorTracePanel";
import { RecordsLogPanel } from "@/features/memory/components/RecordsLogPanel";
import { MEMORY_DEBUG_QUESTION_TABS } from "@/features/memory/components/constants";
import type { MemoryDebugQuestionTab } from "@/features/memory/components/types";
import type { TranslationKey } from "@/i18n";

const REMEMBERED_SUB_TABS: readonly SubTab[] = [
  {
    id: "timeline",
    labelKey: "memoryDebug.viewTimeline",
    Component: MemoryTimelinePanel,
  },
  {
    id: "assertions",
    labelKey: "memoryDebug.sectionAssertions",
    Component: AssertionsViewPanel,
  },
  {
    id: "entities",
    labelKey: "memoryDebug.viewEntities",
    Component: EntitiesBrowsePanel,
  },
  { id: "files", labelKey: "memoryDebug.viewFiles", Component: FileContextPanel },
  { id: "queue", labelKey: "memoryDebug.viewQueue", Component: RecordQueuePanel },
  { id: "links", labelKey: "memoryDebug.viewLinks", Component: MemoryLinksPanel },
];

const ANSWERS_SUB_TABS: readonly SubTab[] = [
  {
    id: "routing",
    labelKey: "memoryDebug.viewRouting",
    Component: RoutingDiagnosticsPanel,
  },
  {
    id: "context",
    labelKey: "memoryDebug.viewContext",
    Component: ContextSnapshotPanel,
  },
];

const BROKEN_SUB_TABS: readonly SubTab[] = [
  { id: "problems", labelKey: "memoryDebug.tabProblems", Component: ProblemsPanel },
  {
    id: "quality",
    labelKey: "memoryDebug.viewQuality",
    Component: QualityDiagnosticsPanel,
  },
  { id: "audit", labelKey: "memoryDebug.viewAudit", Component: AuditDiagnosticsPanel },
  {
    id: "extractor",
    labelKey: "memoryDebug.viewExtractor",
    Component: ExtractorTracePanel,
  },
  {
    id: "recorder",
    labelKey: "memoryDebug.viewRecordsLog",
    Component: RecordsLogPanel,
  },
];

export function MemoryDebugPage() {
  const { t } = useTranslation();
  const [activeTab, setActiveTab] = useState<MemoryDebugQuestionTab>("happening");
  const [refreshToken, setRefreshToken] = useState(0);

  const manualRefreshLabel = useMemo(() => {
    void refreshToken;
    return t("memoryDebug.manualRefreshAt", {
      time: new Date().toLocaleTimeString(),
    });
  }, [refreshToken, t]);

  const refreshPage = (): void => {
    setRefreshToken((previous) => previous + 1);
  };

  return (
    <AdminLayout title={t("memoryDebug.pageTitle")}>
      <div className="flex flex-1 flex-col gap-4 overflow-y-auto p-4 sm:p-6">
        <MemoryStatusStrip refreshToken={refreshToken} />

        <div className="flex flex-wrap items-center justify-between gap-2">
          <nav className="flex gap-2">
            {MEMORY_DEBUG_QUESTION_TABS.map((tab) => (
              <Button
                key={tab.id}
                variant={activeTab === tab.id ? "default" : "outline"}
                size="sm"
                onClick={() => setActiveTab(tab.id)}
              >
                {t(tab.labelKey)}
              </Button>
            ))}
          </nav>
          <div className="flex shrink-0 items-center gap-2">
            <div className="text-xs text-muted-foreground">{manualRefreshLabel}</div>
            <Button variant="outline" size="sm" onClick={refreshPage}>
              {t("memoryDebug.refresh")}
            </Button>
          </div>
        </div>

        {activeTab === "happening" && (
          <MemoryOverviewSection refreshToken={refreshToken} />
        )}

        {activeTab === "remembered" && (
          <SubTabsView tabs={REMEMBERED_SUB_TABS} refreshToken={refreshToken} />
        )}

        {activeTab === "answers" && (
          <SubTabsView tabs={ANSWERS_SUB_TABS} refreshToken={refreshToken} />
        )}

        {activeTab === "broken" && (
          <SubTabsView tabs={BROKEN_SUB_TABS} refreshToken={refreshToken} />
        )}
      </div>
    </AdminLayout>
  );
}

interface SubTab {
  id: string;
  labelKey: TranslationKey;
  Component: React.ComponentType<{
    refreshToken: number;
    onOpenDetail?: (view: string) => void;
  }>;
}

function SubTabsView({
  tabs,
  refreshToken,
}: {
  tabs: readonly SubTab[];
  refreshToken: number;
}) {
  const { t } = useTranslation();
  const [activeSub, setActiveSub] = useState(tabs[0]?.id ?? "");
  const ActiveComponent = tabs.find((tb) => tb.id === activeSub)?.Component;

  return (
    <div className="flex flex-col gap-4">
      <nav className="flex gap-2">
        {tabs.map((tb) => (
          <Button
            key={tb.id}
            variant={activeSub === tb.id ? "default" : "outline"}
            size="sm"
            onClick={() => setActiveSub(tb.id)}
          >
            {t(tb.labelKey)}
          </Button>
        ))}
      </nav>
      {ActiveComponent && (
        <ActiveComponent
          refreshToken={refreshToken}
          // A problem row's "Details" jumps to the named detail sub-tab.
          onOpenDetail={(view) => setActiveSub(view)}
        />
      )}
    </div>
  );
}
