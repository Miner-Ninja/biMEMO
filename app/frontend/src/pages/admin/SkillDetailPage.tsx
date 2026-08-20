import { useEffect, useRef, useState } from "react";
import type { TranslationKey } from "@/i18n";
import { useTranslation } from "react-i18next";
import { useParams, useNavigate, Link } from "react-router-dom";
import {
  Pencil,
  Trash2,
  Check,
  X,
  Plus,
  Activity,
  ArrowLeft,
  CalendarClock,
  RefreshCw,
  History,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";
import { Switch } from "@/components/ui/switch";
import { AdminLayout } from "@/components/layout/AdminLayout";
import { AdminCardSkeleton } from "@/components/ui/skeletons";
import { apiRequest, encodePathSegment } from "@/lib/bimemo-api";
import { cn } from "@/lib/utils";

interface ProcedureStep {
  tool: string;
  query: string;
  note?: string;
}

interface ErrorEntry {
  name: string;
  fix: string;
}

interface Skill {
  id: string;
  name: string;
  title: string;
  description: string;
  trigger: string;
  procedure: ProcedureStep[];
  errors: ErrorEntry[];
  verification: string;
  tool_sequence: string;
  use_count: number;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

interface UsageLog {
  id: string;
  question: string;
  status: string;
  duration_ms: number;
  note: string;
  created_at: string;
}

const TABS: Array<{ key: string; labelKey: TranslationKey }> = [
  { key: "logic", labelKey: "admin.skillLogicTab" },
  { key: "logs", labelKey: "admin.historyTab" },
];

const TOOL_OPTIONS = [
  "web_search",
  "http_fetch",
  "file_read",
  "file_write",
  "file_search",
  "memory_recall",
  "email_send",
  "reminder_create",
  "reminder_list",
  "system_status",
];

function formatDate(iso: string): string {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

const STATUS_STYLES: Record<
  string,
  { bg: string; text: string; border: string; labelKey: TranslationKey }
> = {
  success: {
    bg: "bg-emerald-500/10",
    text: "text-emerald-400",
    border: "border-emerald-500/20",
    labelKey: "admin.statusSuccess",
  },
  partial: {
    bg: "bg-amber-500/10",
    text: "text-amber-400",
    border: "border-amber-500/20",
    labelKey: "admin.statusPartial",
  },
  error: {
    bg: "bg-red-500/10",
    text: "text-red-400",
    border: "border-red-500/20",
    labelKey: "admin.statusError",
  },
};

const STATUS_DOT: Record<string, string> = {
  success: "bg-emerald-500",
  partial: "bg-amber-500",
  error: "bg-red-500",
};

function SkillSection({
  index,
  title,
  children,
}: {
  index: number;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-2xl border border-border/80 bg-card p-5 sm:p-6">
      <div className="mb-4 flex items-center gap-3">
        <div className="flex size-8 shrink-0 items-center justify-center rounded-full border border-primary/30 bg-primary/10 text-sm font-semibold text-primary">
          {index}
        </div>
        <h4 className="text-base font-semibold tracking-tight text-foreground">
          {title}
        </h4>
      </div>
      {children}
    </section>
  );
}

/**
 * Full-page skill editor with hero card and tabbed sections.
 */
export function SkillDetailPage() {
  const { t } = useTranslation();
  const { name } = useParams<{ name: string }>();
  const navigate = useNavigate();

  const [skill, setSkill] = useState<Skill | null>(null);
  const [logs, setLogs] = useState<UsageLog[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState("logic");
  const [editing, setEditing] = useState(false);
  const [editData, setEditData] = useState<Partial<Skill>>({});
  const [showDelete, setShowDelete] = useState(false);
  const [saved, setSaved] = useState(false);
  const [panelMinHeight, setPanelMinHeight] = useState(200);
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!name) return;
    apiRequest<{ skill: Skill; logs: UsageLog[] }>(
      `/admin/skills/${encodePathSegment(name)}`
    )
      .then((d) => {
        setSkill(d.skill);
        setLogs(d.logs || []);
      })
      .catch((e) => setError(e.message || t("admin.loadSkillFailed")))
      .finally(() => setLoading(false));
  }, [name, t]);

  useEffect(() => {
    const panel = panelRef.current;
    if (!panel) return;

    const resizeObserver = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (!entry) return;
      setPanelMinHeight((current) =>
        Math.max(current, Math.ceil(entry.contentRect.height))
      );
    });

    resizeObserver.observe(panel);
    return () => resizeObserver.disconnect();
  }, [activeTab, editing]);

  const startEdit = () => {
    if (!skill) return;
    setEditData({
      title: skill.title,
      trigger: skill.trigger,
      procedure: [...skill.procedure],
      errors: [...skill.errors],
      verification: skill.verification,
    });
    setEditing(true);
  };

  const cancelEdit = () => {
    setEditing(false);
    setEditData({});
  };

  const saveEdit = async () => {
    if (!skill) return;
    try {
      await apiRequest(`/admin/skills/${encodePathSegment(skill.name)}`, {
        method: "PUT",
        body: JSON.stringify(editData),
      });
      setSkill({ ...skill, ...editData } as Skill);
      setEditing(false);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      setError(e instanceof Error ? e.message : t("admin.saveFailed"));
    }
  };

  const handleToggle = async () => {
    if (!skill) return;
    const next = !skill.enabled;
    try {
      await apiRequest(`/admin/skills/${encodePathSegment(skill.name)}`, {
        method: "PUT",
        body: JSON.stringify({ enabled: next ? 1 : 0 }),
      });
      setSkill({ ...skill, enabled: next });
    } catch (e) {
      setError(e instanceof Error ? e.message : t("admin.updateSkillStatusFailed"));
    }
  };

  const handleDelete = async () => {
    if (!skill) return;
    try {
      await apiRequest(`/admin/skills/${encodePathSegment(skill.name)}`, {
        method: "DELETE",
      });
      navigate("/admin/skills");
    } catch (e) {
      setError(e instanceof Error ? e.message : t("admin.deleteFailed"));
    }
  };

  const updateStep = (i: number, key: keyof ProcedureStep, val: string) => {
    setEditData((d) => {
      const proc = [...(d.procedure || [])];
      const step = proc[i];
      if (!step) return d;
      const next: ProcedureStep = { ...step };
      if (key === "note") {
        next.note = val;
      } else {
        next[key] = val;
      }
      proc[i] = next;
      return { ...d, procedure: proc };
    });
  };
  const addStep = () => {
    setEditData((d) => ({
      ...d,
      procedure: [...(d.procedure || []), { tool: "web_search", query: "" }],
    }));
  };
  const removeStep = (i: number) => {
    setEditData((d) => ({
      ...d,
      procedure: (d.procedure || []).filter((_, idx) => idx !== i),
    }));
  };

  const updateError = (i: number, key: keyof ErrorEntry, val: string) => {
    setEditData((d) => {
      const errs = [...(d.errors || [])];
      const entry = errs[i];
      if (!entry) return d;
      const next: ErrorEntry = { ...entry };
      next[key] = val;
      errs[i] = next;
      return { ...d, errors: errs };
    });
  };
  const addError = () => {
    setEditData((d) => ({
      ...d,
      errors: [...(d.errors || []), { name: "", fix: "" }],
    }));
  };
  const removeError = (i: number) => {
    setEditData((d) => ({
      ...d,
      errors: (d.errors || []).filter((_, idx) => idx !== i),
    }));
  };

  if (loading)
    return (
      <AdminLayout title={t("admin.skill")}>
        <div className="p-6">
          <AdminCardSkeleton count={2} />
        </div>
      </AdminLayout>
    );
  if (error && !skill)
    return (
      <AdminLayout title={t("admin.skill")}>
        <div className="p-6 text-center text-destructive">{error}</div>
      </AdminLayout>
    );
  if (!skill)
    return (
      <AdminLayout title={t("admin.skill")}>
        <div className="p-6 text-center text-muted-foreground">
          {t("admin.skillNotFound")}
        </div>
      </AdminLayout>
    );

  const proc = editing ? editData.procedure || [] : skill.procedure;
  const errs = editing ? editData.errors || [] : skill.errors;
  const summaryMetrics = [
    { value: String(skill.use_count), label: t("admin.uses") },
    {
      value: t("admin.stepsCount", { count: skill.procedure.length }),
      label: t("admin.procedure"),
    },
  ];
  const latestLog = logs[0];
  const timeMetrics = [
    {
      label: t("admin.created"),
      value: formatDate(skill.created_at),
      icon: CalendarClock,
    },
    { label: t("admin.updated"), value: formatDate(skill.updated_at), icon: RefreshCw },
    {
      label: t("admin.lastUsed"),
      value: latestLog ? formatDate(latestLog.created_at) : "—",
      icon: History,
    },
  ];

  return (
    <AdminLayout title={skill.title || t("admin.skill")}>
      <div className="flex-1 overflow-y-auto p-4 space-y-5 sm:p-6">
        {/* Top bar */}
        <div className="flex flex-wrap items-center justify-between gap-2">
          <Link
            to="/admin/skills"
            className="inline-flex items-center gap-2 rounded-lg border border-border bg-card px-3 py-2 text-sm font-medium text-foreground transition-colors hover:bg-accent"
          >
            <ArrowLeft className="h-4 w-4" />
            {t("admin.backToSkills")}
          </Link>
          <div className="flex items-center gap-2">
            {saved && (
              <span className="text-xs text-emerald-400 flex items-center gap-1 animate-in fade-in">
                <Check className="h-3.5 w-3.5" /> {t("admin.saved")}
              </span>
            )}
            {editing ? (
              <>
                <Button size="sm" variant="outline" onClick={cancelEdit}>
                  {t("common.cancel")}
                </Button>
                <Button size="sm" className="bg-primary" onClick={saveEdit}>
                  {t("common.save")}
                </Button>
              </>
            ) : (
              <>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={startEdit}
                  className="gap-1.5"
                >
                  <Pencil className="h-3.5 w-3.5" /> {t("admin.edit")}
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => setShowDelete(!showDelete)}
                  className="gap-1.5 border-destructive/30 text-destructive hover:bg-destructive/10 hover:text-destructive"
                >
                  <Trash2 className="h-3.5 w-3.5" /> {t("common.delete")}
                </Button>
              </>
            )}
          </div>
        </div>

        {/* Edit mode banner */}
        {editing && (
          <div className="animate-in fade-in duration-200 rounded-lg border border-primary/20 bg-primary/5 px-4 py-2.5 text-sm text-primary flex items-center gap-2">
            <Pencil className="h-3.5 w-3.5" />
            {t("admin.editModeBanner")}
          </div>
        )}

        {/* Delete confirm */}
        {showDelete && (
          <div className="animate-in fade-in duration-200 rounded-lg border border-destructive/20 bg-destructive/5 px-4 py-3 flex items-center justify-between">
            <span className="text-sm text-destructive">
              {t("admin.deleteSkillConfirm")}
            </span>
            <div className="flex gap-2">
              <Button size="sm" variant="ghost" onClick={() => setShowDelete(false)}>
                {t("common.cancel")}
              </Button>
              <Button size="sm" variant="destructive" onClick={handleDelete}>
                {t("admin.yesDelete")}
              </Button>
            </div>
          </div>
        )}

        {/* Error */}
        {error && (
          <div className="animate-in fade-in duration-200 rounded-lg border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
            {error}
            <button
              type="button"
              className="ml-2 underline"
              onClick={() => setError(null)}
            >
              {t("admin.dismiss")}
            </button>
          </div>
        )}

        {/* Hero card */}
        <div
          className={cn(
            "rounded-xl border bg-card p-0 overflow-hidden transition-colors",
            editing ? "border-primary/30" : "border-border"
          )}
        >
          <div className="flex items-start gap-4 p-6 pb-5">
            {/* Skill icon */}
            <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-primary/20 to-primary/5 border border-primary/20">
              <Activity className="h-6 w-6 text-primary" />
            </div>

            <div className="flex-1 min-w-0">
              {editing ? (
                <Input
                  value={editData.title || ""}
                  onChange={(e) =>
                    setEditData((d) => ({ ...d, title: e.target.value }))
                  }
                  className="text-lg font-semibold mb-1"
                  placeholder={t("admin.skillTitlePlaceholder")}
                />
              ) : (
                <h2 className="text-lg font-semibold leading-tight">{skill.title}</h2>
              )}
              <span className="text-xs text-muted-foreground font-mono mt-1 block">
                {skill.name}
              </span>
            </div>

            <div className="flex items-center gap-2.5 shrink-0">
              <Badge variant={skill.enabled ? "success" : "outline"}>
                {skill.enabled ? t("admin.on") : t("admin.off")}
              </Badge>
              <Switch checked={skill.enabled} onCheckedChange={handleToggle} />
            </div>
          </div>

          {/* Metrics */}
          <div className="border-t border-border px-6 py-5">
            <div className="flex flex-col gap-5 lg:flex-row lg:items-stretch">
              <div className="grid grid-cols-2 gap-3 lg:w-[240px] lg:grid-cols-1">
                {summaryMetrics.map((metric) => (
                  <div
                    key={metric.label}
                    className="rounded-xl border border-border/70 bg-muted/50 px-4 py-3"
                  >
                    <div className="text-2xl font-semibold leading-none">
                      {metric.value}
                    </div>
                    <div className="mt-2 text-[10px] uppercase tracking-[0.2em] text-muted-foreground">
                      {metric.label}
                    </div>
                  </div>
                ))}
              </div>

              <Separator orientation="vertical" className="hidden lg:block" />

              <div className="grid flex-1 gap-3 md:grid-cols-3">
                {timeMetrics.map((metric) => {
                  const Icon = metric.icon;
                  return (
                    <div
                      key={metric.label}
                      className="rounded-xl border border-border/70 bg-muted/50 px-4 py-3.5"
                    >
                      <div className="mb-3 flex items-center justify-between gap-3">
                        <Badge
                          variant="outline"
                          className="uppercase tracking-[0.16em] text-[10px]"
                        >
                          {metric.label}
                        </Badge>
                        <Icon className="h-4 w-4 text-muted-foreground" />
                      </div>
                      <div className="text-lg font-semibold leading-tight text-foreground">
                        {metric.value}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        </div>

        {/* Tabs + Panel connected */}
        <div>
          <div className="relative z-10 flex overflow-x-auto scrollbar-none -mb-px">
            {TABS.map((tab) => (
              <button
                key={tab.key}
                type="button"
                onClick={() => {
                  const currentHeight = panelRef.current?.offsetHeight ?? 0;
                  setPanelMinHeight((current) => Math.max(current, currentHeight));
                  setActiveTab(tab.key);
                }}
                className={cn(
                  "relative shrink-0 rounded-t-lg border px-3.5 py-3 transition-colors sm:px-5",
                  activeTab === tab.key
                    ? "border-border border-b-transparent bg-card text-foreground"
                    : "border-transparent text-muted-foreground hover:text-foreground"
                )}
              >
                <h4 className="text-base font-semibold tracking-tight">
                  {t(tab.labelKey)}
                </h4>
              </button>
            ))}
          </div>

          <div
            ref={panelRef}
            style={{ minHeight: `${panelMinHeight}px` }}
            className={cn(
              "animate-in fade-in duration-200 rounded-xl rounded-tl-none border bg-card p-7 transition-colors",
              editing ? "border-primary/30" : "border-border"
            )}
          >
            {activeTab === "logic" && (
              <div className="flex flex-col gap-4">
                <SkillSection index={1} title={t("admin.trigger")}>
                  {editing ? (
                    <Textarea
                      value={editData.trigger || ""}
                      onChange={(e) =>
                        setEditData((d) => ({ ...d, trigger: e.target.value }))
                      }
                      rows={5}
                      placeholder={t("admin.triggerPlaceholder")}
                    />
                  ) : (
                    <div className="rounded-xl border border-border/70 bg-muted/50 p-4 sm:p-5">
                      <p className="text-sm leading-relaxed text-foreground/85">
                        {skill.trigger || t("admin.noTriggerDescription")}
                      </p>
                    </div>
                  )}
                </SkillSection>

                <SkillSection index={2} title={t("admin.toolsAndArguments")}>
                  <div className="space-y-3">
                    {proc.map((step, i) => (
                      <div
                        key={i}
                        className="rounded-xl border border-border/70 bg-muted/50 p-4 sm:p-5"
                      >
                        <div className="grid gap-4 sm:grid-cols-[180px_1fr] sm:gap-5">
                          <div className="sm:border-r sm:border-border/60 sm:pr-5">
                            <div className="grid grid-cols-[32px_1fr] gap-x-3 gap-y-4">
                              <div className="row-span-2 mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full border-2 border-primary bg-primary/10 text-xs font-bold text-primary">
                                {i + 1}
                              </div>

                              <div>
                                <div className="text-[10px] font-medium uppercase tracking-[0.16em] text-muted-foreground">
                                  {t("admin.step")}
                                </div>
                                <div className="mt-1.5 text-sm font-semibold text-foreground">
                                  {t("admin.toolCall")}
                                </div>
                              </div>

                              <div>
                                <div className="text-[10px] font-medium uppercase tracking-[0.16em] text-muted-foreground">
                                  {t("admin.tool")}
                                </div>
                                {editing ? (
                                  <div className="mt-2 space-y-2">
                                    <select
                                      value={step.tool}
                                      onChange={(e) =>
                                        updateStep(i, "tool", e.target.value)
                                      }
                                      className="w-full rounded-md border border-border bg-background px-2.5 py-1.5 text-xs font-mono"
                                    >
                                      {TOOL_OPTIONS.map((t) => (
                                        <option key={t} value={t}>
                                          {t}
                                        </option>
                                      ))}
                                    </select>
                                    <button
                                      type="button"
                                      onClick={() => removeStep(i)}
                                      className="inline-flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-destructive"
                                    >
                                      <X className="h-3.5 w-3.5" />
                                      {t("admin.removeStep")}
                                    </button>
                                  </div>
                                ) : (
                                  <Badge variant="info" className="mt-2 text-[11px]">
                                    {step.tool}
                                  </Badge>
                                )}
                              </div>
                            </div>
                          </div>

                          <div className="space-y-4">
                            <div>
                              <div className="text-[10px] font-medium uppercase tracking-[0.16em] text-muted-foreground">
                                {t("admin.arguments")}
                              </div>
                              {editing ? (
                                <Input
                                  value={step.query}
                                  onChange={(e) =>
                                    updateStep(i, "query", e.target.value)
                                  }
                                  placeholder={t("admin.queryPlaceholder")}
                                  className="mt-2 text-sm"
                                />
                              ) : (
                                <div className="mt-2 text-sm leading-relaxed text-foreground/85">
                                  {step.query}
                                </div>
                              )}
                            </div>

                            {step.note && (
                              <>
                                <Separator />
                                <div>
                                  <div className="text-xs italic leading-relaxed text-muted-foreground">
                                    {step.note}
                                  </div>
                                </div>
                              </>
                            )}
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                  {editing && (
                    <button
                      type="button"
                      onClick={addStep}
                      className="mt-4 flex items-center gap-1.5 text-sm text-primary hover:underline"
                    >
                      <Plus className="h-3.5 w-3.5" /> {t("admin.addStep")}
                    </button>
                  )}
                </SkillSection>

                <SkillSection index={3} title={t("admin.issuesAndFixes")}>
                  <div className="space-y-3">
                    {errs.map((err, i) => (
                      <div
                        key={i}
                        className="rounded-xl border border-border/70 bg-muted/50 p-4"
                      >
                        {editing ? (
                          <div className="space-y-3">
                            <div className="flex items-center justify-between">
                              <label className="text-[10px] font-medium uppercase tracking-[0.06em] text-amber-400">
                                {t("common.error")}
                              </label>
                              <button
                                type="button"
                                onClick={() => removeError(i)}
                                className="flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-destructive"
                              >
                                <X className="h-3 w-3" /> {t("admin.remove")}
                              </button>
                            </div>
                            <Input
                              value={err.name}
                              onChange={(e) => updateError(i, "name", e.target.value)}
                              placeholder={t("admin.errorNamePlaceholder")}
                              className="font-medium"
                            />
                            <label className="block text-[10px] font-medium uppercase tracking-[0.06em] text-emerald-400">
                              {t("admin.solution")}
                            </label>
                            <Textarea
                              value={err.fix}
                              onChange={(e) => updateError(i, "fix", e.target.value)}
                              rows={2}
                              placeholder={t("admin.errorFixPlaceholder")}
                            />
                          </div>
                        ) : (
                          <div className="grid gap-3 sm:grid-cols-[180px_1fr] sm:items-start">
                            <div className="flex items-center gap-2">
                              <Badge
                                variant="warning"
                                className="text-xs font-semibold"
                              >
                                {err.name}
                              </Badge>
                            </div>
                            <div className="text-sm leading-relaxed text-muted-foreground">
                              {err.fix}
                            </div>
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                  {editing && (
                    <button
                      type="button"
                      onClick={addError}
                      className="mt-4 flex items-center gap-1.5 text-sm text-primary hover:underline"
                    >
                      <Plus className="h-3.5 w-3.5" /> {t("admin.addError")}
                    </button>
                  )}
                </SkillSection>

                <SkillSection index={4} title={t("admin.resultVerification")}>
                  {editing ? (
                    <Textarea
                      value={editData.verification || ""}
                      onChange={(e) =>
                        setEditData((d) => ({ ...d, verification: e.target.value }))
                      }
                      rows={5}
                      placeholder={t("admin.verificationPlaceholder")}
                    />
                  ) : (
                    <div className="rounded-xl border border-border/70 bg-muted/50 p-4 sm:p-5">
                      <p className="text-sm leading-relaxed text-foreground/85">
                        {skill.verification || t("admin.noVerificationCriteria")}
                      </p>
                    </div>
                  )}
                </SkillSection>
              </div>
            )}

            {activeTab === "logs" && (
              <div>
                <div className="flex items-center justify-between mb-4">
                  <div className="text-[10px] uppercase tracking-[0.08em] text-muted-foreground font-medium">
                    {t("admin.usageHistory")}
                  </div>
                  <Badge variant="outline">
                    {t("admin.entriesCount", { count: logs.length })}
                  </Badge>
                </div>
                {logs.length === 0 ? (
                  <p className="text-sm text-muted-foreground py-8 text-center">
                    {t("admin.noUsageRecorded")}
                  </p>
                ) : (
                  <div className="space-y-3">
                    {logs.map((log) => {
                      const style =
                        STATUS_STYLES[log.status] || STATUS_STYLES["success"];
                      const dot = STATUS_DOT[log.status] || STATUS_DOT["success"];
                      if (!style) return null;
                      return (
                        <div
                          key={log.id}
                          className="rounded-xl border border-border bg-muted/30 p-4"
                        >
                          <div className="flex items-center justify-between mb-2.5">
                            <div className="flex items-center gap-2.5">
                              <span
                                className={cn("h-2.5 w-2.5 rounded-full shrink-0", dot)}
                              />
                              <span className="text-xs text-muted-foreground font-mono">
                                {formatDate(log.created_at)}
                              </span>
                              <Badge
                                className={cn(
                                  "text-[10px]",
                                  style.bg,
                                  style.text,
                                  style.border
                                )}
                              >
                                {t(style.labelKey)}
                              </Badge>
                            </div>
                            <span className="text-xs text-muted-foreground">
                              {formatDuration(log.duration_ms)}
                            </span>
                          </div>
                          <div className="text-sm text-foreground/85 leading-relaxed">
                            {log.question}
                          </div>
                          {log.note && (
                            <div
                              className={cn(
                                "text-xs mt-2.5 pt-2.5 border-t border-border flex items-start gap-1.5",
                                log.status === "error"
                                  ? "text-red-400"
                                  : "text-amber-400"
                              )}
                            >
                              <span className="shrink-0 mt-0.5">→</span>
                              {log.note}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </AdminLayout>
  );
}
