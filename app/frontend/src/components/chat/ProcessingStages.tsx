import { memo } from "react";
import { useTranslation } from "react-i18next";
import { ChevronRight } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { LiveTimer } from "./LiveTimer";
import { ShimmerText } from "./ShimmerText";
import { cn } from "@/lib/utils";
import type { ProcessingStage } from "@/types/chat";

interface ProcessingStagesProps {
  stages: ProcessingStage[];
  className?: string;
}

const LASER_EASING = "cubic-bezier(0.2, 0.6, 0.25, 1)";

const LaserScanner = memo(() => (
  <div aria-hidden className="relative h-2 w-full">
    <div className="absolute inset-x-0 top-1/2 h-[2px] -translate-y-1/2 rounded-full bg-red-500/25" />
    <div
      className="absolute inset-y-0 left-0 w-full"
      style={{
        animation: `laser-bullet 1.8s ${LASER_EASING} infinite alternate`,
        willChange: "transform",
      }}
    >
      <div
        className="absolute top-1/2 left-0 h-2 w-2 -translate-y-1/2 rounded-full bg-white"
        style={{
          animation: "laser-pulse 0.45s ease-in-out infinite alternate",
          willChange: "box-shadow",
        }}
      />
    </div>
  </div>
));
LaserScanner.displayName = "LaserScanner";

/**
 * Shows only the current (last) processing stage.
 * Animates transition when stage changes via key-based remount.
 */
export const ProcessingStages = memo(function ProcessingStages({
  stages,
  className,
}: ProcessingStagesProps) {
  const { t } = useTranslation();
  if (stages.length === 0) return null;

  const current = stages[stages.length - 1];

  return (
    <div className={cn("px-2 py-2 sm:px-4 sm:py-3", className)}>
      <Card className="border-border/70 bg-card/80 shadow-none">
        <CardContent
          className={cn(
            "flex min-w-0 items-center gap-3 p-3 sm:p-4",
            current.status === "completed" && "text-muted-foreground",
            current.status === "error" && "text-destructive"
          )}
        >
          <div className="flex shrink-0 items-center gap-3">
            <ChevronRight
              className="h-3.5 w-3.5 shrink-0 text-muted-foreground"
              aria-hidden
            />
            <span className="shrink-0 truncate text-sm">
              {current.status === "active" ? (
                <ShimmerText text={current.label} />
              ) : (
                current.label
              )}
            </span>
            {current.status === "completed" && (
              <span
                key={`${current.id}-ok`}
                className="shrink-0 text-sm text-muted-foreground animate-in fade-in zoom-in-50 duration-300"
                aria-label={t("chat.stageCompleted")}
              >
                ✓
              </span>
            )}
            {current.status === "error" && (
              <span
                key={`${current.id}-err`}
                className="shrink-0 text-sm text-destructive animate-in fade-in zoom-in-50 duration-300"
                aria-label={t("chat.stageError")}
              >
                ✕
              </span>
            )}
          </div>
          <div className="min-w-0 flex-1">
            <LaserScanner />
          </div>
          <LiveTimer
            startedAt={current.startedAt}
            frozenMs={current.frozenDurationMs}
          />
        </CardContent>
      </Card>
    </div>
  );
});
