import { useTranslation } from "react-i18next";
import { MEMORY_DEBUG_SECTIONS } from "@/features/memory/components/constants";
import type { MemoryDebugSection } from "@/features/memory/components/types";
import { Button } from "@/components/ui/button";

/**
 * Single-row section switcher. Descriptions live in tooltips so data
 * stays visible on the first screen (audit: three navigation floors).
 */
export function MemoryDebugSectionNav({
  activeSection,
  onSectionChange,
}: {
  activeSection: MemoryDebugSection;
  onSectionChange: (section: MemoryDebugSection) => void;
}) {
  const { t } = useTranslation();
  return (
    <div
      role="tablist"
      aria-label={t("memoryDebug.sectionsAriaLabel")}
      className="flex flex-wrap gap-2"
    >
      {MEMORY_DEBUG_SECTIONS.map((section) => (
        <Button
          key={section.id}
          role="tab"
          aria-selected={section.id === activeSection}
          variant={section.id === activeSection ? "default" : "outline"}
          size="sm"
          title={t(section.descriptionKey)}
          onClick={() => onSectionChange(section.id)}
        >
          {t(section.labelKey)}
        </Button>
      ))}
    </div>
  );
}
