import { ArrowLeft } from "lucide-react";
import { Link } from "react-router-dom";

import { Button } from "@/components/ui/button";

export function MemoryDebugHeader({
  onRefresh,
  lastUpdatedLabel,
}: {
  onRefresh: () => void;
  lastUpdatedLabel: string;
}) {
  return (
    <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
      <div className="flex items-start gap-3">
        <Link to="/admin" className="mt-1 text-muted-foreground hover:text-foreground">
          <ArrowLeft className="h-5 w-5" />
        </Link>
        <div className="space-y-1">
          <h1 className="text-2xl font-bold tracking-tight">Memory Debug</h1>
          <p className="max-w-3xl text-sm text-muted-foreground">
            Review memory health, inspect stored data, and trace how messages become
            records and summaries.
          </p>
        </div>
      </div>
      <div className="flex items-center gap-2">
        <div className="text-xs text-muted-foreground">{lastUpdatedLabel}</div>
        <Button variant="outline" size="sm" onClick={onRefresh}>
          Refresh
        </Button>
      </div>
    </div>
  );
}
