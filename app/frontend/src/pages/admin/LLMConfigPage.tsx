import { useEffect, useState } from "react";
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

export function LLMConfigPage() {
  const [configs, setConfigs] = useState<LLMConfigEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
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

  const reload = async () => {
    try {
      const data = await listLLMConfigs();
      setConfigs(data.configs);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load configurations");
    }
  };

  useEffect(() => {
    reload().finally(() => setLoading(false));
  }, []);

  const handleCreate = () => {
    setEditConfig(null);
    setDialogOpen(true);
  };

  const handleEdit = (config: LLMConfigEntry) => {
    setEditConfig(config);
    setDialogOpen(true);
  };

  const handleSave = async (data: {
    service_type: ProviderType;
    api_key?: string;
    model?: string;
    dialog_recorder_model?: string;
    base_url?: string;
    dialog_recorder_base_url?: string;
  }) => {
    setError("");
    setSuccess("");
    try {
      if (editConfig) {
        await updateLLMConfig(editConfig.id, data);
        setSuccess("Configuration updated.");
      } else {
        await createLLMConfig(data);
        setSuccess("Configuration created.");
      }
      setDialogOpen(false);
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save configuration");
    }
  };

  const handleDelete = async (config: LLMConfigEntry) => {
    setDeleteTarget(config);
  };

  const confirmDelete = async () => {
    if (!deleteTarget) return;
    setError("");
    setSuccess("");
    try {
      await deleteLLMConfig(deleteTarget.id);
      setSuccess("Configuration deleted.");
      setDeleteTarget(null);
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete configuration");
    }
  };

  const handleActivate = async (config: LLMConfigEntry) => {
    setError("");
    setSuccess("");
    try {
      const result = await activateLLMConfig(config.id);
      if (result.reload?.success) {
        const provider = result.reload.active_provider ?? "unknown";
        const model = result.reload.model ?? "";
        setSuccess(`Configuration applied — ${provider}: ${model}`);
      } else if (result.reload?.error) {
        setSuccess(
          `Activated, but reload failed: ${result.reload.error}. Restart to apply.`
        );
      } else {
        setSuccess("Configuration activated. Restart the service to apply.");
      }
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to activate configuration");
    }
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
          error: err instanceof Error ? err.message : "Test failed",
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
    <AdminLayout title="LLM Configuration">
      <div className="flex-1 overflow-y-auto p-6 space-y-6 max-w-2xl">
        <div className="flex items-center justify-between">
          <p className="text-sm text-muted-foreground">
            {loading
              ? "Loading…"
              : activeConfig
                ? `Active: ${activeConfig.service_type} — ${activeConfig.model || "no model"}`
                : "No active provider"}
          </p>
          <Button onClick={handleCreate} size="sm">
            <Plus className="h-4 w-4 mr-1" />
            Add Provider
          </Button>
        </div>

        <ErrorAlert error={error} />

        {success && (
          <p className="text-sm text-green-600 dark:text-green-400">{success}</p>
        )}

        {!loading && configs.length === 0 && (
          <div className="rounded-lg border border-dashed border-border p-8 text-center space-y-3">
            <p className="text-muted-foreground">No LLM providers configured</p>
            <Button onClick={handleCreate}>
              <Plus className="h-4 w-4 mr-2" />
              Add Provider
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
              <h3 className="font-semibold mb-2">Delete Configuration</h3>
              <p className="text-sm text-muted-foreground mb-4">
                Are you sure you want to delete the {deleteTarget.service_type}{" "}
                configuration
                {deleteTarget.model ? ` (${deleteTarget.model})` : ""}?
                {deleteTarget.enabled && " This is the active provider."}
              </p>
              <div className="flex gap-3 justify-end">
                <Button variant="ghost" onClick={() => setDeleteTarget(null)}>
                  Cancel
                </Button>
                <Button variant="destructive" onClick={confirmDelete}>
                  Delete
                </Button>
              </div>
            </div>
          </div>
        )}
      </div>
    </AdminLayout>
  );
}
