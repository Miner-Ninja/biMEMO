import { useState, useCallback, useRef } from "react";
import type { ProcessingStage } from "@/types/chat";

export function useProcessingStages() {
  const [processingStages, setProcessingStages] = useState<ProcessingStage[]>([]);
  const stagesRef = useRef<ProcessingStage[]>([]);
  const stageCounter = useRef(0);

  const updateStages = useCallback((stages: ProcessingStage[]) => {
    stagesRef.current = stages;
    setProcessingStages(stages);
  }, []);

  const addStage = useCallback(
    (label: string): void => {
      const now = performance.now();
      const prev = stagesRef.current;
      const updated = prev.map((s) =>
        s.status === "active"
          ? { ...s, status: "completed" as const, frozenDurationMs: now - s.startedAt }
          : s
      );
      stageCounter.current += 1;
      updateStages([
        ...updated,
        {
          id: `stage-${stageCounter.current}`,
          label,
          status: "active" as const,
          startedAt: now,
        },
      ]);
    },
    [updateStages]
  );

  const completeActiveStage = useCallback((): void => {
    const now = performance.now();
    updateStages(
      stagesRef.current.map((s) =>
        s.status === "active"
          ? { ...s, status: "completed" as const, frozenDurationMs: now - s.startedAt }
          : s
      )
    );
  }, [updateStages]);

  const errorActiveStage = useCallback((): void => {
    const now = performance.now();
    updateStages(
      stagesRef.current.map((s) =>
        s.status === "active"
          ? { ...s, status: "error" as const, frozenDurationMs: now - s.startedAt }
          : s
      )
    );
  }, [updateStages]);

  const clearStages = useCallback((): void => {
    updateStages([]);
    stageCounter.current = 0;
  }, [updateStages]);

  return {
    processingStages,
    stagesRef,
    addStage,
    completeActiveStage,
    errorActiveStage,
    clearStages,
  };
}
