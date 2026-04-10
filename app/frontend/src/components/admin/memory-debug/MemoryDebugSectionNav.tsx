import { MEMORY_DEBUG_SECTIONS } from "@/components/admin/memory-debug/constants"
import type { MemoryDebugSection } from "@/components/admin/memory-debug/types"
import { cn } from "@/lib/utils"

export function MemoryDebugSectionNav({
  activeSection,
  onSectionChange,
}: {
  activeSection: MemoryDebugSection
  onSectionChange: (section: MemoryDebugSection) => void
}) {
  return (
    <div
      role="tablist"
      aria-label="Memory debug sections"
      className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4"
    >
      {MEMORY_DEBUG_SECTIONS.map((section) => (
        <button
          key={section.id}
          role="tab"
          aria-selected={section.id === activeSection}
          onClick={() => onSectionChange(section.id)}
          className={cn(
            "rounded-xl border px-4 py-3 text-left transition-all",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
            section.id === activeSection
              ? "border-primary/50 bg-primary/8 shadow-sm"
              : "border-border bg-background hover:border-primary/40 hover:bg-muted/40",
          )}
        >
          <div className="text-lg font-semibold tracking-tight">{section.label}</div>
          <div
            className={cn(
              "mt-1 text-sm leading-5",
              section.id === activeSection ? "text-foreground/75" : "text-muted-foreground",
            )}
          >
            {section.description}
          </div>
        </button>
      ))}
    </div>
  )
}
