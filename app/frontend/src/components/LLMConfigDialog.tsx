import { useEffect, useState } from "react";
import type { TranslationKey } from "@/i18n";
import { useTranslation } from "react-i18next";
import { X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ModelCombobox } from "@/components/ModelCombobox";
import { testLLMConnection, getModelList } from "@/api/llm";
import type { LLMConfigEntry, ProviderType } from "@/api/llm";

const PROVIDER_LABEL_KEYS: Record<ProviderType, TranslationKey> = {
  openrouter: "admin.providerOpenrouter",
  ollama: "admin.providerOllama",
  custom: "admin.providerCustom",
};

const PROVIDER_SUBTITLE_KEYS: Partial<Record<ProviderType, TranslationKey>> = {
  custom: "admin.openaiCompatibleApi",
};

type TabId = "chat" | "recorder" | "vectors";

const TAB_LABEL_KEYS: Record<TabId, TranslationKey> = {
  chat: "admin.tabChat",
  recorder: "admin.tabRecorder",
  vectors: "admin.tabVectors",
};

/** Outcome of the latest model-list load for one tab. */
type LoadState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "ok"; count: number }
  | { status: "empty" }
  | { status: "error"; detail?: string };

const IDLE: LoadState = { status: "idle" };

interface LLMConfigDialogProps {
  open: boolean;
  onClose: () => void;
  onSave: (data: {
    service_type: ProviderType;
    api_key?: string;
    model?: string;
    dialog_recorder_model?: string;
    base_url?: string;
    dialog_recorder_base_url?: string;
    embedding_model?: string;
    embedding_base_url?: string;
    num_ctx?: string;
  }) => Promise<void>;
  editConfig?: LLMConfigEntry | null;
}

export function LLMConfigDialog({
  open,
  onClose,
  onSave,
  editConfig,
}: LLMConfigDialogProps) {
  const { t } = useTranslation();
  const isEdit = !!editConfig;

  const [tab, setTab] = useState<TabId>("chat");

  const [serviceType, setServiceType] = useState<ProviderType>("openrouter");
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState("");
  const [dialogRecorderModel, setDialogRecorderModel] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [dialogRecorderBaseUrl, setDialogRecorderBaseUrl] = useState("");
  const [embeddingModel, setEmbeddingModel] = useState("");
  const [embeddingBaseUrl, setEmbeddingBaseUrl] = useState("");

  // Each tab owns its pick-list: its host may differ from the others.
  const [tabModels, setTabModels] = useState<Record<TabId, string[]>>({
    chat: [],
    recorder: [],
    vectors: [],
  });
  const [tabLoad, setTabLoad] = useState<Record<TabId, LoadState>>({
    chat: IDLE,
    recorder: IDLE,
    vectors: IDLE,
  });

  const [testResult, setTestResult] = useState("");
  const [testOk, setTestOk] = useState(false);
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);

  const setLoad = (target: TabId, state: LoadState) =>
    setTabLoad((prev) => ({ ...prev, [target]: state }));

  const clearLoadError = (target: TabId) =>
    setTabLoad((prev) =>
      prev[target].status === "error" ? { ...prev, [target]: IDLE } : prev
    );

  const urlForTab = (target: TabId, overrides?: { chatUrl?: string }) => {
    const chatUrl = overrides?.chatUrl ?? baseUrl;
    if (target === "recorder") return dialogRecorderBaseUrl || chatUrl;
    if (target === "vectors") return embeddingBaseUrl || chatUrl;
    return chatUrl;
  };

  const loadTabModels = async (
    target: TabId,
    opts?: {
      service_type?: ProviderType;
      api_key?: string;
      base_url?: string;
    }
  ) => {
    setLoad(target, { status: "loading" });
    try {
      const result = await getModelList({
        service_type: opts?.service_type ?? serviceType,
        api_key: opts?.api_key ?? (apiKey || undefined),
        base_url: opts?.base_url ?? (urlForTab(target) || undefined),
      });
      if (result.error) {
        setLoad(target, { status: "error", detail: result.error });
        return;
      }
      setTabModels((prev) => ({ ...prev, [target]: result.models }));
      setLoad(
        target,
        result.models.length
          ? { status: "ok", count: result.models.length }
          : { status: "empty" }
      );
    } catch (err) {
      setLoad(target, {
        status: "error",
        detail: err instanceof Error ? err.message : undefined,
      });
    }
  };

  useEffect(() => {
    if (open) {
      if (editConfig) {
        setServiceType(editConfig.service_type);
        setModel(editConfig.model ?? "");
        setDialogRecorderModel(editConfig.dialog_recorder_model ?? "");
        setBaseUrl(editConfig.base_url ?? "");
        setDialogRecorderBaseUrl(editConfig.dialog_recorder_base_url ?? "");
        setEmbeddingModel(editConfig.embedding_model ?? "");
        setEmbeddingBaseUrl(editConfig.embedding_base_url ?? "");
        setApiKey("");
      } else {
        setServiceType("openrouter");
        setApiKey("");
        setModel("");
        setDialogRecorderModel("");
        setBaseUrl("");
        setDialogRecorderBaseUrl("");
        setEmbeddingModel("");
        setEmbeddingBaseUrl("");
      }
      setTab("chat");
      setTabModels({ chat: [], recorder: [], vectors: [] });
      setTabLoad({ chat: IDLE, recorder: IDLE, vectors: IDLE });
      setTestResult("");
      setTestOk(false);
      // Preload each tab's pick-list from its own host so every model
      // field offers a choice without a manual click.
      const activeType = editConfig?.service_type ?? "openrouter";
      if (activeType === "ollama" || activeType === "custom") {
        const chatUrl = editConfig?.base_url || "";
        void loadTabModels("chat", {
          service_type: activeType,
          base_url: chatUrl || undefined,
        });
        void loadTabModels("recorder", {
          service_type: activeType,
          base_url: editConfig?.dialog_recorder_base_url || chatUrl || undefined,
        });
        void loadTabModels("vectors", {
          service_type: activeType,
          base_url: editConfig?.embedding_base_url || chatUrl || undefined,
        });
      }
    }
    // loadTabModels reads only state that this effect just reset.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, editConfig]);

  const showApiKey = serviceType === "openrouter" || serviceType === "custom";
  const showBaseUrl = serviceType === "ollama" || serviceType === "custom";
  const providerSubtitleKey = PROVIDER_SUBTITLE_KEYS[serviceType];

  const handleTest = async () => {
    setTesting(true);
    setTestResult("");
    setTestOk(false);
    try {
      const result = await testLLMConnection({
        service_type: serviceType,
        api_key: apiKey || undefined,
        model: model || undefined,
        base_url: baseUrl || undefined,
      });
      setTestOk(result.ok);
      setTestResult(
        result.ok
          ? t("admin.connectedTo", { provider: result.provider ?? serviceType })
          : t("admin.connectionFailedWithError", {
              error: result.error ?? t("admin.unknown"),
            })
      );
    } catch (err) {
      setTestOk(false);
      setTestResult(err instanceof Error ? err.message : t("admin.testFailed"));
    } finally {
      setTesting(false);
    }
  };

  const handleSubmit = async () => {
    setSaving(true);
    try {
      await onSave({
        service_type: serviceType,
        api_key: apiKey || undefined,
        model: model || undefined,
        dialog_recorder_model: dialogRecorderModel || undefined,
        base_url: baseUrl || undefined,
        dialog_recorder_base_url: dialogRecorderBaseUrl || undefined,
        embedding_model: embeddingModel || undefined,
        embedding_base_url: embeddingBaseUrl || undefined,
        num_ctx: editConfig?.num_ctx,
      });
    } finally {
      setSaving(false);
    }
  };

  if (!open) return null;

  const loadStatusLine = (target: TabId) => {
    const state = tabLoad[target];
    if (state.status === "ok") {
      return (
        <p className="text-xs text-green-600 dark:text-green-400">
          {t("admin.modelsLoaded", { count: state.count })}
        </p>
      );
    }
    if (state.status === "empty") {
      return (
        <p className="text-xs text-muted-foreground">{t("admin.modelsListEmpty")}</p>
      );
    }
    if (state.status === "error") {
      return (
        <p className="text-xs text-red-600 dark:text-red-400">
          {t("admin.modelsLoadFailed")}
          {state.detail ? ` (${state.detail})` : ""}
        </p>
      );
    }
    return null;
  };

  const modelFieldHeader = (
    target: TabId,
    labelKey: TranslationKey,
    optional: boolean
  ) => (
    <div className="flex items-center justify-between">
      <label className="text-sm font-medium">
        {t(labelKey)}{" "}
        {optional && (
          <span className="text-muted-foreground font-normal">
            {t("admin.optional")}
          </span>
        )}
      </label>
      <button
        type="button"
        onClick={() => void loadTabModels(target)}
        disabled={tabLoad[target].status === "loading"}
        className="text-xs text-muted-foreground hover:text-foreground disabled:opacity-50"
      >
        {tabLoad[target].status === "loading"
          ? t("common.loading")
          : t("admin.loadModels")}
      </button>
    </div>
  );

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="fixed inset-0 bg-black/50 backdrop-blur-sm" onClick={onClose} />
      <div className="relative z-50 w-full max-w-lg rounded-lg border border-border bg-background p-6 shadow-lg mx-4 max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold">
            {isEdit ? t("admin.editLlmConfigTitle") : t("admin.addLlmProviderTitle")}
          </h2>
          <button
            onClick={onClose}
            className="text-muted-foreground hover:text-foreground"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="flex gap-1 border-b border-border mb-4">
          {(Object.keys(TAB_LABEL_KEYS) as TabId[]).map((id) => (
            <button
              key={id}
              type="button"
              onClick={() => setTab(id)}
              className={`px-3 py-2 text-sm border-b-2 -mb-px ${
                tab === id
                  ? "border-primary font-medium"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              }`}
            >
              {t(TAB_LABEL_KEYS[id])}
              {tabLoad[id].status === "error" && (
                <span className="ml-1 text-red-600 dark:text-red-400">•</span>
              )}
            </button>
          ))}
        </div>

        <div className="space-y-4">
          {/* All panels stay mounted, stacked in one grid cell: hidden
              tabs keep their height, so the dialog never resizes when
              switching tabs. */}
          <div className="grid">
            <div
              className={`col-start-1 row-start-1 space-y-4 ${
                tab === "chat" ? "" : "invisible pointer-events-none"
              }`}
              aria-hidden={tab !== "chat"}
            >
              <div className="space-y-1">
                <label className="text-sm font-medium">{t("admin.provider")}</label>
                <select
                  value={serviceType}
                  onChange={(e) => {
                    setServiceType(e.target.value as ProviderType);
                    setTabModels({ chat: [], recorder: [], vectors: [] });
                    setTabLoad({ chat: IDLE, recorder: IDLE, vectors: IDLE });
                  }}
                  disabled={isEdit}
                  className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
                >
                  {(Object.keys(PROVIDER_LABEL_KEYS) as ProviderType[]).map((key) => (
                    <option key={key} value={key}>
                      {t(PROVIDER_LABEL_KEYS[key])}
                    </option>
                  ))}
                </select>
                {providerSubtitleKey && (
                  <p className="text-xs text-muted-foreground">
                    {t(providerSubtitleKey)}
                  </p>
                )}
              </div>

              {showApiKey && (
                <div className="space-y-1">
                  <label className="text-sm font-medium">{t("admin.apiKey")}</label>
                  <Input
                    type="password"
                    name="bimemo-llm-api-key"
                    placeholder={
                      isEdit && editConfig?.has_api_key
                        ? t("admin.apiKeyConfiguredPlaceholder")
                        : "sk-…"
                    }
                    value={apiKey}
                    onChange={(e) => setApiKey(e.target.value)}
                    onBlur={() => {
                      if (apiKey) void loadTabModels("chat");
                    }}
                    autoComplete="new-password"
                  />
                  {isEdit && editConfig?.has_api_key && !apiKey && (
                    <p className="text-xs text-muted-foreground">
                      {t("admin.apiKeySavedHint")}
                    </p>
                  )}
                </div>
              )}

              {showBaseUrl && (
                <div className="space-y-1">
                  <label className="text-sm font-medium">{t("admin.baseUrl")}</label>
                  <Input
                    placeholder={
                      serviceType === "ollama"
                        ? "http://ollama:11434"
                        : "https://api.example.com/v1"
                    }
                    value={baseUrl}
                    onChange={(e) => {
                      setBaseUrl(e.target.value);
                      clearLoadError("chat");
                    }}
                    onBlur={() => {
                      if (serviceType === "ollama" || baseUrl)
                        void loadTabModels("chat");
                    }}
                  />
                </div>
              )}

              <div className="space-y-1">
                {modelFieldHeader("chat", "admin.model", false)}
                <ModelCombobox
                  value={model}
                  onChange={setModel}
                  models={tabModels.chat}
                  loading={tabLoad.chat.status === "loading"}
                  placeholder={
                    serviceType === "openrouter"
                      ? "openai/gpt-4o"
                      : serviceType === "ollama"
                        ? "llama3.2"
                        : "model-name"
                  }
                />
                {loadStatusLine("chat")}
              </div>
            </div>

            <div
              className={`col-start-1 row-start-1 space-y-4 ${
                tab === "recorder" ? "" : "invisible pointer-events-none"
              }`}
              aria-hidden={tab !== "recorder"}
            >
              <p className="text-xs text-muted-foreground">
                {t("admin.recorderTabHint")}
              </p>
              <div className="space-y-1">
                <label className="text-sm font-medium">
                  {t("admin.dialogRecorderBaseUrl")}{" "}
                  <span className="text-muted-foreground font-normal">
                    {t("admin.optional")}
                  </span>
                </label>
                <Input
                  placeholder={
                    serviceType === "ollama"
                      ? "http://ollama-memory:11434"
                      : "http://vllm-dialog-recorder:8000"
                  }
                  value={dialogRecorderBaseUrl}
                  onChange={(e) => {
                    setDialogRecorderBaseUrl(e.target.value);
                    clearLoadError("recorder");
                  }}
                />
              </div>
              <div className="space-y-1">
                {modelFieldHeader("recorder", "admin.dialogRecorderModel", true)}
                <ModelCombobox
                  value={dialogRecorderModel}
                  onChange={setDialogRecorderModel}
                  models={tabModels.recorder}
                  loading={tabLoad.recorder.status === "loading"}
                  placeholder={t("admin.sameModelPlaceholder")}
                />
                {loadStatusLine("recorder")}
              </div>
            </div>

            <div
              className={`col-start-1 row-start-1 space-y-4 ${
                tab === "vectors" ? "" : "invisible pointer-events-none"
              }`}
              aria-hidden={tab !== "vectors"}
            >
              <p className="text-xs text-muted-foreground">
                {t("admin.vectorsTabHint")}
              </p>
              <div className="space-y-1">
                <label className="text-sm font-medium">
                  {t("admin.embeddingBaseUrl")}{" "}
                  <span className="text-muted-foreground font-normal">
                    {t("admin.optional")}
                  </span>
                </label>
                <Input
                  placeholder={
                    serviceType === "ollama"
                      ? "http://ollama-memory:11434"
                      : "http://vllm-embeddings:8000"
                  }
                  value={embeddingBaseUrl}
                  onChange={(e) => {
                    setEmbeddingBaseUrl(e.target.value);
                    clearLoadError("vectors");
                  }}
                />
              </div>
              <div className="space-y-1">
                {modelFieldHeader("vectors", "admin.embeddingModel", true)}
                <ModelCombobox
                  value={embeddingModel}
                  onChange={setEmbeddingModel}
                  models={tabModels.vectors}
                  loading={tabLoad.vectors.status === "loading"}
                  placeholder={t("admin.embeddingModelPlaceholder")}
                />
                {loadStatusLine("vectors")}
              </div>
            </div>
          </div>

          {testResult && (
            <p
              className={`text-sm ${testOk ? "text-green-600 dark:text-green-400" : "text-red-600 dark:text-red-400"}`}
            >
              {testResult}
            </p>
          )}

          <div className="flex items-center gap-3 pt-2">
            <Button onClick={handleSubmit} disabled={saving}>
              {saving
                ? t("admin.savingEllipsis")
                : isEdit
                  ? t("admin.update")
                  : t("admin.create")}
            </Button>
            <Button variant="ghost" onClick={handleTest} disabled={testing}>
              {testing ? t("admin.testing") : t("admin.testConnection")}
            </Button>
            <Button variant="ghost" onClick={onClose}>
              {t("common.cancel")}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
