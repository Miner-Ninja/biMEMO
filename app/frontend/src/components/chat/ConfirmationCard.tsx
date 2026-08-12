import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Check,
  CheckCircle2,
  Clock3,
  Database,
  Globe,
  Search,
  ShieldAlert,
  Terminal,
  Wrench,
  X,
  XCircle,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { confirmAction, denyAction } from "@/lib/bimemo-api";
import type { TranslationKey } from "@/i18n";
import type { PendingConfirmation } from "@/types/chat";
import { cn } from "@/lib/utils";

// Values are translation keys, resolved with t() at render time.
const TOOL_LABEL_KEYS: Record<string, TranslationKey> = {
  http_fetch: "chat.toolFetchUrl",
  email_send: "chat.toolSendEmail",
};

// Map known tool names to a recognisable icon + display label.
const TOOL_ICONS: Record<string, { icon: typeof Terminal; labelKey?: TranslationKey }> =
  {
    bash: { icon: Terminal },
    run_command: { icon: Terminal },
    project_command: { icon: Terminal },
    git_status: { icon: Terminal },
    git_diff: { icon: Terminal },
    git_log: { icon: Terminal },
    web_search: { icon: Search },
    http_fetch: { icon: Globe, labelKey: "chat.toolFetchUrl" },
    file_read: { icon: Database },
    file_write: { icon: Database },
    file_search: { icon: Search },
    email_send: { icon: Globe, labelKey: "chat.toolSendEmail" },
  };

const CONFIRMATION_TIMEOUT_MS = 300_000;
const PARAMETER_PREVIEW_LIMIT = 120;

interface ConfirmationCardProps {
  confirmation: PendingConfirmation;
  onStatusChange: (actionId: string, status: PendingConfirmation["status"]) => void;
}

// Per-state look: an accent bar + tint + icon that read the outcome at a glance.
type StateStyle = {
  frame: string;
  iconWrap: string;
  icon: typeof ShieldAlert;
  badge: "success" | "danger" | "warning" | "secondary";
  headingKey: TranslationKey;
  statusKey: TranslationKey;
};

const STATE_STYLES: Record<PendingConfirmation["status"], StateStyle> = {
  pending: {
    frame: "border-l-amber-500 bg-amber-500/[0.06] ring-amber-500/20",
    iconWrap: "bg-amber-500/15 text-amber-500",
    icon: ShieldAlert,
    badge: "warning",
    headingKey: "chat.approvalRequired",
    statusKey: "chat.pendingApproval",
  },
  approved: {
    frame: "border-l-emerald-500 bg-emerald-500/[0.06] ring-emerald-500/20",
    iconWrap: "bg-emerald-500/15 text-emerald-500",
    icon: CheckCircle2,
    badge: "success",
    headingKey: "chat.approved",
    statusKey: "chat.approved",
  },
  denied: {
    frame: "border-l-red-500 bg-red-500/[0.06] ring-red-500/20",
    iconWrap: "bg-red-500/15 text-red-500",
    icon: XCircle,
    badge: "danger",
    headingKey: "chat.denied",
    statusKey: "chat.denied",
  },
  expired: {
    frame: "border-l-zinc-500 bg-zinc-500/[0.05] ring-zinc-500/20",
    iconWrap: "bg-zinc-500/15 text-zinc-400",
    icon: Clock3,
    badge: "secondary",
    headingKey: "chat.expired",
    statusKey: "chat.expired",
  },
};

function buildParameterSummary(parameters: PendingConfirmation["parameters"]): string {
  return Object.entries(parameters)
    .filter(([, value]) => typeof value === "string")
    .map(([key, value]) => `${key}: ${String(value).slice(0, PARAMETER_PREVIEW_LIMIT)}`)
    .join("  ·  ");
}

export default function ConfirmationCard({
  confirmation,
  onStatusChange,
}: ConfirmationCardProps) {
  const { t } = useTranslation();
  const [isLoading, setIsLoading] = useState(false);
  const isResolved = confirmation.status !== "pending";
  const style = STATE_STYLES[confirmation.status];
  const StateIcon = style.icon;

  // Resolve tool icon + label from the tool name
  const toolMeta = TOOL_ICONS[confirmation.tool_name] ?? { icon: Wrench };
  const ToolIcon = toolMeta.icon;
  const labelKey = toolMeta.labelKey ?? TOOL_LABEL_KEYS[confirmation.tool_name];
  const label = labelKey ? t(labelKey) : confirmation.tool_name;
  const parameterSummary = buildParameterSummary(confirmation.parameters);

  useEffect(() => {
    if (isResolved) {
      return undefined;
    }

    const timer = window.setTimeout(() => {
      onStatusChange(confirmation.action_id, "expired");
    }, CONFIRMATION_TIMEOUT_MS);

    return () => window.clearTimeout(timer);
  }, [confirmation.action_id, isResolved, onStatusChange]);

  const handleApprove = async () => {
    setIsLoading(true);
    try {
      await confirmAction(confirmation.action_id);
      onStatusChange(confirmation.action_id, "approved");
    } catch {
      onStatusChange(confirmation.action_id, "expired");
    } finally {
      setIsLoading(false);
    }
  };

  const handleDeny = async () => {
    setIsLoading(true);
    try {
      await denyAction(confirmation.action_id);
      onStatusChange(confirmation.action_id, "denied");
    } catch {
      onStatusChange(confirmation.action_id, "expired");
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div
      className={cn(
        "w-full max-w-xl overflow-hidden rounded-xl border border-l-4 border-border/60 shadow-sm ring-1",
        style.frame,
        isResolved && "opacity-90"
      )}
    >
      <div className="flex items-start gap-3 p-4">
        <div
          className={cn(
            "mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg",
            style.iconWrap
          )}
        >
          <StateIcon className="h-5 w-5" />
        </div>

        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-2">
            <h3 className="text-sm font-semibold text-foreground">
              {t(style.headingKey)}
            </h3>
            <Badge variant={style.badge}>{t(style.statusKey)}</Badge>
          </div>

          {!isResolved && (
            <p className="mt-0.5 text-xs text-muted-foreground">
              {t("chat.approvalRequiredHint")}
            </p>
          )}

          {/* What will run — a command-style preview */}
          <div className="mt-3 rounded-lg border border-border/60 bg-muted/50 px-3 py-2">
            <div className="flex items-center gap-2.5">
              <ToolIcon className="h-5 w-5 shrink-0 text-foreground" />
              <span className="truncate font-medium text-sm text-foreground">
                {label}
              </span>
            </div>
            {parameterSummary && (
              <div className="mt-1 truncate pl-[22px] font-mono text-xs text-muted-foreground">
                {parameterSummary}
              </div>
            )}
          </div>

          {isResolved ? (
            <p className="mt-3 text-xs text-muted-foreground">
              {t("chat.decisionRecorded")}
            </p>
          ) : (
            <div className="mt-3 flex justify-end gap-2">
              <Button
                size="sm"
                onClick={handleApprove}
                disabled={isLoading}
                className="gap-1.5"
              >
                <Check className="h-4 w-4" />
                {t("chat.allow")}
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={handleDeny}
                disabled={isLoading}
                className="gap-1.5"
              >
                <X className="h-4 w-4" />
                {t("chat.deny")}
              </Button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
