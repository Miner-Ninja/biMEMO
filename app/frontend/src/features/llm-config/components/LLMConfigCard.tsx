import { useTranslation } from "react-i18next";
import type { TranslationKey } from "@/i18n";
import {
  Cpu,
  Globe,
  Server,
  Power,
  Pencil,
  Trash2,
  Plug,
  Loader2,
  Key,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import type { LLMConfigEntry } from "@/features/llm-config/api/llm";

const PROVIDER_LABEL_KEYS: Record<string, TranslationKey> = {
  openrouter: "admin.providerOpenrouter",
  ollama: "admin.providerOllama",
  custom: "admin.providerCustom",
};

const PROVIDER_SUBTITLE_KEYS: Record<string, TranslationKey> = {
  custom: "admin.openaiCompatibleApi",
};

const PROVIDER_ICONS: Record<string, typeof Cpu> = {
  openrouter: Globe,
  ollama: Server,
  custom: Cpu,
};

interface LLMConfigCardProps {
  config: LLMConfigEntry;
  onEdit: () => void;
  onDelete: () => void;
  onActivate: () => void;
  onTest: () => void;
  testResult?: { ok: boolean; error?: string } | null;
  testing?: boolean;
}

export function LLMConfigCard({
  config,
  onEdit,
  onDelete,
  onActivate,
  onTest,
  testResult,
  testing,
}: LLMConfigCardProps) {
  const { t } = useTranslation();
  const Icon = PROVIDER_ICONS[config.service_type] ?? Cpu;
  const labelKey = PROVIDER_LABEL_KEYS[config.service_type];
  const label = labelKey ? t(labelKey) : config.service_type;
  const subtitleKey = PROVIDER_SUBTITLE_KEYS[config.service_type];

  return (
    <div
      className={`rounded-lg border p-4 space-y-3 ${config.enabled ? "border-primary/50 bg-primary/5" : "border-border"}`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-3 min-w-0">
          <Icon className="h-5 w-5 shrink-0 text-muted-foreground" />
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="font-medium truncate">{label}</span>
              {config.enabled ? (
                <span className="inline-flex items-center rounded-full bg-green-100 px-2 py-0.5 text-xs font-medium text-green-700 dark:bg-green-900/30 dark:text-green-400">
                  {t("admin.active")}
                </span>
              ) : (
                <span className="inline-flex items-center rounded-full bg-muted px-2 py-0.5 text-xs font-medium text-muted-foreground">
                  {t("admin.inactive")}
                </span>
              )}
            </div>
            {config.model && (
              <p className="text-sm text-muted-foreground truncate">{config.model}</p>
            )}
            {subtitleKey && (
              <p className="text-xs text-muted-foreground truncate">{t(subtitleKey)}</p>
            )}
            {config.dialog_recorder_model && (
              <p className="text-xs text-muted-foreground truncate">
                {t("admin.dialogRecorderValue", {
                  value: config.dialog_recorder_model,
                })}
              </p>
            )}
          </div>
        </div>
      </div>

      <div className="flex items-center gap-2 flex-wrap">
        {config.has_api_key && (
          <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
            <Key className="h-3 w-3" />
            {t("admin.apiKeyConfigured")}
          </span>
        )}
        {config.base_url && (
          <span className="text-xs text-muted-foreground truncate max-w-[200px]">
            {config.base_url}
          </span>
        )}
        {config.dialog_recorder_base_url && (
          <span className="text-xs text-muted-foreground truncate max-w-[200px]">
            {t("admin.dialogRecorderValue", {
              value: config.dialog_recorder_base_url,
            })}
          </span>
        )}
      </div>

      {testResult && (
        <p
          className={`text-xs ${testResult.ok ? "text-green-600 dark:text-green-400" : "text-red-600 dark:text-red-400"}`}
        >
          {testResult.ok
            ? t("admin.connected")
            : testResult.error || t("admin.connectionFailed")}
        </p>
      )}

      <div className="flex items-center gap-1 pt-1">
        {!config.enabled && (
          <Button
            variant="ghost"
            size="sm"
            onClick={onActivate}
            className="h-7 px-2 text-xs"
          >
            <Power className="h-3.5 w-3.5 mr-1" />
            {t("admin.activate")}
          </Button>
        )}
        <Button variant="ghost" size="sm" onClick={onEdit} className="h-7 px-2 text-xs">
          <Pencil className="h-3.5 w-3.5 mr-1" />
          {t("admin.edit")}
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={onTest}
          disabled={testing}
          className="h-7 px-2 text-xs"
        >
          {testing ? (
            <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" />
          ) : (
            <Plug className="h-3.5 w-3.5 mr-1" />
          )}
          {t("admin.test")}
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={onDelete}
          className="h-7 px-2 text-xs text-destructive hover:text-destructive"
        >
          <Trash2 className="h-3.5 w-3.5 mr-1" />
          {t("common.delete")}
        </Button>
      </div>
    </div>
  );
}
