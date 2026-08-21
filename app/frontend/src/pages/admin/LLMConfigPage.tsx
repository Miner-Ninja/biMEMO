import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { AdminLayout } from "@/components/layout/AdminLayout";
import { ErrorAlert } from "@/components/ui/error-alert";
import { LLMConfigCard } from "@/components/LLMConfigCard";
import { LLMConfigDialog } from "@/components/LLMConfigDialog";
import {
  listLLMConfigs,
  createLLMConfig,
  updateLLMConfig,
  deleteLLMConfig,
  activateLLMConfig,
  testSavedLLMConfig,
} from "@/api/llm";
import type { LLMConfigEntry, ProviderType } from "@/api/llm";
import { queryClient } from "@/lib/query-client";
import { QUERY_KEYS, errorToDisplayText } from "@/hooks/use-server-state";

export function LLMConfigPage() {
  const { t } = useTranslation();
  const [manualError, setManualError] = useState("");
  const {
    data: configsData,
    isInitialLoading: loading,
    error: queryError,
  } = useQuery({
    queryKey: QUERY_KEYS.llmConfig,
    queryFn: listLLMConfigs,
  });
  const configs = configsData?.configs ?? [];
  const error = manualError || (queryError ? errorToDisplayText(queryError, t) : "");
  const [success, setSuccess] = useState("");

  // Dialog state
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editConfig, setEditConfig] = useState<LLMConfigEntry | null>(null);

  // Delete confirmation
  const [deleteTarget, setDeleteTarget] = useState<LLMConfigEntry | null>(null);

  // Test results per config id
  const [testResults, setTestResults] = useState<
    Record<string, { ok: boolean; error?: string }>
  >({});
  const [testingIds, setTestingIds] = useState<Set<string>>(new Set());

  const saveMutation = useMutation({
    mutationFn: async (data: {
      service_type: ProviderType;
      api_key?: string;
      model?: string;
      dialog_recorder_model?: string;
      base_url?: string;
      dialog_recorder_base_url?: string;
    }) => {
      // Both responses are unused here (the success message depends only
      // on whether this is an edit); normalize to a single void type.
      if (editConfig) {
        await updateLLMConfig(editConfig.id, data);
      } else {
        await createLLMConfig(data);
      }
    },
    onSuccess: () => {
      if (editConfig) setSuccess(t("admin.configUpdated"));
      else setSuccess(t("admin.configCreated"));
      setDialogOpen(false);
      void queryClient.invalidateQueries({ queryKey: QUERY_KEYS.llmConfig });
    },
    onError: (err) =>
      setManualError(err instanceof Error ? err.message : t("admin.saveConfigFailed")),
  });

  const handleSave = async (data: {
    service_type: ProviderType;
    api_key?: string;
    model?: string;
    dialog_recorder_model?: string;
    base_url?: string;
    dialog_recorder_base_url?: string;
  }) => {
    setManualError("");
    setSuccess("");
    try {
      await saveMutation.mutateAsync(data);
    } catch {
      // display handled by the mutation's onError
    }
  };

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteLLMConfig(id),
    onSuccess: () => {
      setSuccess(t("admin.configDeleted"));
      setDeleteTarget(null);
      void queryClient.invalidateQueries({ queryKey: QUERY_KEYS.llmConfig });
    },
    onError: (err) =>
      setManualError(
        err instanceof Error ? err.message : t("admin.deleteConfigFailed")
      ),
  });

  const confirmDelete = async () => {
    if (!deleteTarget) return;
    setManualError("");
    setSuccess("");
    try {
      await deleteMutation.mutateAsync(deleteTarget.id);
    } catch {
      // display handled by the mutation's onError
    }
  };

  const activateMutation = useMutation({
    mutationFn: (id: string) => activateLLMConfig(id),
    onSuccess: (result) => {
      if (result.reload?.success) {
        const provider = result.reload.active_provider ?? t("admin.unknown");
        const model = result.reload.model ?? "";
        setSuccess(t("admin.configApplied", { provider, model }));
      } else if (result.reload?.error) {
        setSuccess(
          t("admin.configActivatedReloadFailed", { error: result.reload.error })
        );
      } else {
        setSuccess(t("admin.configActivated"));
      }
      void queryClient.invalidateQueries({ queryKey: QUERY_KEYS.llmConfig });
    },
    onError: (err) =>
      setManualError(
        err instanceof Error ? err.message : t("admin.activateConfigFailed")
      ),
  });

  const handleActivate = async (config: LLMConfigEntry) => {
    setManualError("");
    setSuccess("");
    try {
      await activateMutation.mutateAsync(config.id);
    } catch {
      // display handled by the mutation's onError
    }
  };

  const handleCreate = () => {
    setEditConfig(null);
    setDialogOpen(true);
  };

  const handleEdit = (config: LLMConfigEntry) => {
    setEditConfig(config);
    setDialogOpen(true);
  };

  const handleDelete = async (config: LLMConfigEntry) => {
    setDeleteTarget(config);
  };

  const handleTest = async (config: LLMConfigEntry) => {
    setTestingIds((prev) => new Set(prev).add(config.id));
    setTestResults((prev) => {
      const next = { ...prev };
      delete next[config.id];
      return next;
    });
    try {
      const result = await testSavedLLMConfig(config.id);
      setTestResults((prev) => ({ ...prev, [config.id]: result }));
    } catch (err) {
      setTestResults((prev) => ({
        ...prev,
        [config.id]: {
          ok: false,
          error: err instanceof Error ? err.message : t("admin.testFailed"),
        },
      }));
    } finally {
      setTestingIds((prev) => {
        const next = new Set(prev);
        next.delete(config.id);
        return next;
      });
    }
  };

  const activeConfig = configs.find((c) => c.enabled);

  return (
    <AdminLayout title={t("admin.llmConfiguration")}>
      <div className="flex-1 overflow-y-auto p-6 space-y-6 max-w-2xl">
        <div className="flex items-center justify-between">
          <p className="text-sm text-muted-foreground">
            {loading
              ? t("common.loading")
              : activeConfig
                ? t("admin.activeProviderLine", {
                    provider: activeConfig.service_type,
                    model: activeConfig.model || t("admin.noModel"),
                  })
                : t("admin.noActiveProvider")}
          </p>
          <Button onClick={handleCreate} size="sm">
            <Plus className="h-4 w-4 mr-1" />
            {t("admin.addProvider")}
          </Button>
        </div>

        <ErrorAlert error={error} />

        {success && (
          <p className="text-sm text-green-600 dark:text-green-400">{success}</p>
        )}

        {!loading && configs.length === 0 && (
          <div className="rounded-lg border border-dashed border-border p-8 text-center space-y-3">
            <p className="text-muted-foreground">{t("admin.noProvidersConfigured")}</p>
            <Button onClick={handleCreate}>
              <Plus className="h-4 w-4 mr-2" />
              {t("admin.addProvider")}
            </Button>
          </div>
        )}

        {!loading && configs.length > 0 && (
          <div className="space-y-3">
            {configs.map((config) => (
              <LLMConfigCard
                key={config.id}
                config={config}
                onEdit={() => handleEdit(config)}
                onDelete={() => handleDelete(config)}
                onActivate={() => handleActivate(config)}
                onTest={() => handleTest(config)}
                testResult={testResults[config.id] ?? null}
                testing={testingIds.has(config.id)}
              />
            ))}
          </div>
        )}

        <LLMConfigDialog
          open={dialogOpen}
          onClose={() => setDialogOpen(false)}
          onSave={handleSave}
          editConfig={editConfig}
        />

        {/* Delete confirmation */}
        {deleteTarget && (
          <div className="fixed inset-0 z-50 flex items-center justify-center">
            <div
              className="fixed inset-0 bg-black/50 backdrop-blur-sm"
              onClick={() => setDeleteTarget(null)}
            />
            <div className="relative z-50 w-full max-w-sm rounded-lg border border-border bg-background p-6 shadow-lg mx-4">
              <h3 className="font-semibold mb-2">{t("admin.deleteConfigTitle")}</h3>
              <p className="text-sm text-muted-foreground mb-4">
                {t("admin.deleteConfigConfirm", {
                  provider: deleteTarget.service_type,
                  model: deleteTarget.model ? ` (${deleteTarget.model})` : "",
                })}
                {deleteTarget.enabled && t("admin.deleteConfigActiveWarning")}
              </p>
              <div className="flex gap-3 justify-end">
                <Button variant="ghost" onClick={() => setDeleteTarget(null)}>
                  {t("common.cancel")}
                </Button>
                <Button variant="destructive" onClick={confirmDelete}>
                  {t("common.delete")}
                </Button>
              </div>
            </div>
          </div>
        )}
      </div>
    </AdminLayout>
  );
}
