import { useState } from "react";

import { confirmAction, denyAction } from "@/lib/bimemo-api";
import type { PendingConfirmation } from "@/types/chat";

const TOOL_LABELS: Record<string, string> = {
  http_fetch: "Fetch URL",
  email_send: "Send Email",
};

const CONFIRMATION_TIMEOUT_MS = 300_000;

interface ConfirmationCardProps {
  confirmation: PendingConfirmation;
  onStatusChange: (actionId: string, status: PendingConfirmation["status"]) => void;
}

export default function ConfirmationCard({
  confirmation,
  onStatusChange,
}: ConfirmationCardProps) {
  const [loading, setLoading] = useState(false);

  const isResolved = confirmation.status !== "pending";
  const label = TOOL_LABELS[confirmation.tool_name] || confirmation.tool_name;

  const paramSummary = Object.entries(confirmation.parameters)
    .filter(([, v]) => typeof v === "string")
    .map(([k, v]) => `${k}: ${String(v).slice(0, 80)}`)
    .join(", ");

  const handleApprove = async () => {
    setLoading(true);
    try {
      await confirmAction(confirmation.action_id);
      onStatusChange(confirmation.action_id, "approved");
    } catch {
      onStatusChange(confirmation.action_id, "expired");
    } finally {
      setLoading(false);
    }
  };

  const handleDeny = async () => {
    setLoading(true);
    try {
      await denyAction(confirmation.action_id);
      onStatusChange(confirmation.action_id, "denied");
    } catch {
      onStatusChange(confirmation.action_id, "expired");
    } finally {
      setLoading(false);
    }
  };

  // Auto-expire after timeout
  useState(() => {
    if (isResolved) return;
    const timer = setTimeout(() => {
      onStatusChange(confirmation.action_id, "expired");
    }, CONFIRMATION_TIMEOUT_MS);
    return () => clearTimeout(timer);
  });

  const statusColors: Record<string, string> = {
    approved: "border-green-600 bg-green-950/30",
    denied: "border-red-600 bg-red-950/30",
    expired: "border-zinc-600 bg-zinc-950/30 opacity-60",
    pending: "border-yellow-600 bg-yellow-950/20",
  };

  const statusLabels: Record<string, string> = {
    approved: "Approved",
    denied: "Denied",
    expired: "Expired",
  };

  return (
    <div
      className={`my-2 rounded-lg border p-3 text-sm ${statusColors[confirmation.status] || statusColors.pending}`}
    >
      <div className="mb-1 font-medium">{label}</div>
      {paramSummary && (
        <div className="mb-2 truncate text-xs text-muted-foreground">
          {paramSummary}
        </div>
      )}
      {isResolved ? (
        <div className="text-xs font-medium">
          {statusLabels[confirmation.status]}
        </div>
      ) : (
        <div className="flex gap-2">
          <button
            onClick={handleApprove}
            disabled={loading}
            className="rounded bg-green-700 px-3 py-1 text-xs font-medium text-white hover:bg-green-600 disabled:opacity-50"
          >
            Allow
          </button>
          <button
            onClick={handleDeny}
            disabled={loading}
            className="rounded bg-zinc-700 px-3 py-1 text-xs font-medium text-white hover:bg-zinc-600 disabled:opacity-50"
          >
            Deny
          </button>
        </div>
      )}
    </div>
  );
}
