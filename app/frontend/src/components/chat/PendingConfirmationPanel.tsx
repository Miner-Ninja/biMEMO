import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { getToolMeta } from "@/lib/tool-metadata";
import { cn } from "@/lib/utils";
import type { ConfirmationScope, PendingConfirmation } from "@/types/chat";

interface PendingConfirmationPanelProps {
  confirmation: PendingConfirmation;
  onDecide: (decision: "approve" | "deny", scope: ConfirmationScope) => void;
  busy: boolean;
  actionable?: boolean;
  className?: string;
}

/** Compact one-line preview of the tool's key argument. */
function parameterPreview(parameters: Record<string, unknown>): string {
  const entries = Object.entries(parameters).filter(
    ([, value]) => typeof value === "string" && value !== ""
  );
  if (!entries.length) return "";
  const first = entries[0];
  if (!first) return "";
  const text = String(first[1]);
  return text.length > 120 ? `${text.slice(0, 120)}…` : text;
}

/**
 * Pinned panel above the chat input for one pending tool confirmation.
 * Every button IS a complete decision; the set follows the server-supplied
 * payload fields only (grant_capable, url_host) — the client never inspects
 * tool names or arguments to decide which buttons exist.
 */
export function PendingConfirmationPanel({
  confirmation,
  onDecide,
  busy,
  className,
  actionable = true,
}: PendingConfirmationPanelProps) {
  const { t } = useTranslation();
  const meta = getToolMeta(confirmation.tool_name);
  const Icon = meta.icon;
  const toolLabel = meta.labelKey ? t(meta.labelKey) : confirmation.tool_name;
  const preview = parameterPreview(confirmation.parameters);
  const grantCapable = confirmation.grant_capable === true;
  const urlHost = confirmation.url_host ?? null;

  return (
    <div className="mx-auto w-full max-w-[1920px] shrink-0 px-1.5 sm:px-0 2xl:px-[10%]">
      <div
        className={cn(
          "mx-auto mb-2 w-full max-w-3xl rounded-lg border p-3",
          actionable
            ? "border-amber-500/40 bg-amber-500/5"
            : "border-border bg-muted/50",
          className
        )}
        data-testid="pending-confirmation-panel"
      >
        <div className="flex items-center gap-2 text-sm font-medium">
          <Icon className="h-4 w-4 shrink-0" />
          <span>{t("common.confirmPanelTitle")}</span>
          <span className="text-muted-foreground">{toolLabel}</span>
        </div>
        {preview && (
          <p className="mt-1 truncate font-mono text-xs text-muted-foreground">
            {preview}
          </p>
        )}
        {actionable ? (
          <div className="mt-2 flex flex-wrap justify-end gap-2">
            <Button
              size="sm"
              disabled={busy}
              onClick={() => onDecide("approve", "once")}
            >
              {t("common.confirmBtnOnce")}
            </Button>
            {grantCapable && urlHost && (
              <Button
                size="sm"
                variant="secondary"
                disabled={busy}
                onClick={() => onDecide("approve", "domain")}
              >
                {t("common.confirmBtnDomain", { host: urlHost })}
              </Button>
            )}
            {grantCapable && (
              <Button
                size="sm"
                variant="secondary"
                disabled={busy}
                onClick={() => onDecide("approve", "conversation")}
              >
                {t("common.confirmBtnConversation")}
              </Button>
            )}
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() => onDecide("deny", "once")}
            >
              {t("common.confirmBtnDeny")}
            </Button>
          </div>
        ) : (
          <p className="mt-2 text-xs text-muted-foreground">
            {t("common.confirmExpired")}
          </p>
        )}
      </div>
    </div>
  );
}
