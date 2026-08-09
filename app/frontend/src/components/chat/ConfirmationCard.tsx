import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { CheckCircle2, Clock3, OctagonAlert, ShieldAlert, XCircle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { confirmAction, denyAction } from "@/lib/bimemo-api";
import type { TranslationKey } from "@/i18n";
import type { PendingConfirmation } from "@/types/chat";
import { cn } from "@/lib/utils";

// Values are translation keys, resolved with t() at render time.
const TOOL_LABEL_KEYS: Record<string, TranslationKey> = {
  http_fetch: "chat.toolFetchUrl",
  email_send: "chat.toolSendEmail",
};

const CONFIRMATION_TIMEOUT_MS = 300_000;
const PARAMETER_PREVIEW_LIMIT = 80;

interface ConfirmationCardProps {
  confirmation: PendingConfirmation;
  onStatusChange: (actionId: string, status: PendingConfirmation["status"]) => void;
}

function getConfirmationStatusVariant(
  status: PendingConfirmation["status"]
): "success" | "danger" | "warning" | "secondary" {
  switch (status) {
    case "approved":
      return "success";
    case "denied":
      return "danger";
    case "expired":
      return "secondary";
    case "pending":
      return "warning";
  }
}

// Returns a translation key, resolved with t() at render time.
function getConfirmationStatusLabelKey(
  status: PendingConfirmation["status"]
): TranslationKey {
  switch (status) {
    case "approved":
      return "chat.approved";
    case "denied":
      return "chat.denied";
    case "expired":
      return "chat.expired";
    case "pending":
      return "chat.pendingApproval";
  }
}

function getConfirmationStatusIcon(status: PendingConfirmation["status"]) {
  switch (status) {
    case "approved":
      return CheckCircle2;
    case "denied":
      return XCircle;
    case "expired":
      return Clock3;
    case "pending":
      return ShieldAlert;
  }
}

function buildParameterSummary(parameters: PendingConfirmation["parameters"]): string {
  return Object.entries(parameters)
    .filter(([, value]) => typeof value === "string")
    .map(([key, value]) => `${key}: ${String(value).slice(0, PARAMETER_PREVIEW_LIMIT)}`)
    .join(", ");
}

export default function ConfirmationCard({
  confirmation,
  onStatusChange,
}: ConfirmationCardProps) {
  const { t } = useTranslation();
  const [isLoading, setIsLoading] = useState(false);

  const isResolved = confirmation.status !== "pending";
  const labelKey = TOOL_LABEL_KEYS[confirmation.tool_name];
  const label = labelKey ? t(labelKey) : confirmation.tool_name;
  const parameterSummary = buildParameterSummary(confirmation.parameters);
  const StatusIcon = getConfirmationStatusIcon(confirmation.status);

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
    <Card
      className={cn("my-2 border-border/70 bg-card/80", isResolved && "opacity-85")}
    >
      <CardHeader className="gap-3 p-4 pb-3">
        <div className="flex items-start justify-between gap-3">
          <div className="flex min-w-0 items-start gap-2.5">
            <div className="mt-0.5 rounded-md border border-border/70 bg-muted/60 p-1.5 text-muted-foreground">
              <StatusIcon className="h-4 w-4" />
            </div>
            <div className="min-w-0">
              <CardTitle>{label}</CardTitle>
              {parameterSummary ? (
                <div className="mt-1 text-sm text-muted-foreground">
                  {parameterSummary}
                </div>
              ) : null}
            </div>
          </div>
          <Badge variant={getConfirmationStatusVariant(confirmation.status)}>
            {t(getConfirmationStatusLabelKey(confirmation.status))}
          </Badge>
        </div>
      </CardHeader>
      <Separator />
      <CardContent className="p-4 pt-4">
        {isResolved ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <OctagonAlert className="h-4 w-4" />
            {t("chat.decisionRecorded")}
          </div>
        ) : (
          <div className="flex flex-wrap gap-2">
            <Button onClick={handleApprove} disabled={isLoading}>
              {t("chat.allow")}
            </Button>
            <Button variant="outline" onClick={handleDeny} disabled={isLoading}>
              {t("chat.deny")}
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
