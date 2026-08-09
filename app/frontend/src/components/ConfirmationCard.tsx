import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { ShieldAlert } from "lucide-react";
import { Button } from "@/components/ui/button";

interface ConfirmationCardProps {
  actionId: string;
  toolName: string;
  parameters: Record<string, unknown>;
  timeoutSeconds: number;
  onConfirm: (actionId: string, approved: boolean) => void;
}

export function ConfirmationCard({
  actionId,
  toolName,
  parameters,
  timeoutSeconds,
  onConfirm,
}: ConfirmationCardProps) {
  const { t } = useTranslation();
  const [remaining, setRemaining] = useState(timeoutSeconds);
  const [decided, setDecided] = useState(false);

  useEffect(() => {
    if (decided || remaining <= 0) return;
    const timer = setInterval(() => {
      setRemaining((prev) => {
        if (prev <= 1) {
          clearInterval(timer);
          return 0;
        }
        return prev - 1;
      });
    }, 1000);
    return () => clearInterval(timer);
  }, [decided, remaining]);

  const handleDecision = (approved: boolean) => {
    setDecided(true);
    onConfirm(actionId, approved);
  };

  return (
    <div className="mx-4 my-2 rounded-lg border-2 border-amber-500 bg-amber-50 p-4 dark:border-amber-600 dark:bg-amber-950/30">
      <div className="flex items-start gap-3">
        <ShieldAlert className="h-6 w-6 shrink-0 text-amber-600 dark:text-amber-500" />
        <div className="flex-1 space-y-3">
          <div>
            <h3 className="font-semibold text-amber-900 dark:text-amber-200">
              {t("chat.actionConfirmationRequired")}
            </h3>
            <p className="text-sm text-amber-800 dark:text-amber-300">
              <span className="font-mono font-medium">{toolName}</span>{" "}
              {t("chat.wantsToExecute")}
            </p>
          </div>

          <div className="rounded bg-amber-100 p-3 font-mono text-sm dark:bg-amber-900/40">
            {Object.entries(parameters).map(([key, value]) => (
              <div key={key} className="break-all">
                <span className="text-amber-700 dark:text-amber-400">{key}:</span>{" "}
                <span className="text-amber-900 dark:text-amber-200">
                  {typeof value === "string" ? value : JSON.stringify(value)}
                </span>
              </div>
            ))}
          </div>

          <div className="flex items-center justify-between">
            <div className="flex gap-2">
              <Button
                variant="default"
                size="sm"
                onClick={() => handleDecision(true)}
                disabled={decided || remaining <= 0}
              >
                {t("chat.approve")}
              </Button>
              <Button
                variant="destructive"
                size="sm"
                onClick={() => handleDecision(false)}
                disabled={decided || remaining <= 0}
              >
                {t("chat.deny")}
              </Button>
            </div>
            {!decided && remaining > 0 && (
              <span className="text-xs text-amber-700 dark:text-amber-400">
                {t("chat.expiresIn", { seconds: remaining })}
              </span>
            )}
            {remaining <= 0 && !decided && (
              <span className="text-xs text-destructive">{t("chat.timedOut")}</span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
