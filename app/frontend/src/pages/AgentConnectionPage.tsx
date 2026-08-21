import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  Download,
  Plus,
  Trash2,
  Copy,
  Check,
  Shield,
  Key,
  Settings,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { AdminLayout } from "@/components/layout/AdminLayout";
import { AdminCardSkeleton } from "@/components/ui/skeletons";
import { Input } from "@/components/ui/input";
import { ErrorAlert } from "@/components/ui/error-alert";
import { ReauthModal } from "@/components/ReauthModal";
import {
  listAgentTokens,
  issueAgentToken,
  revokeAgentToken,
  type IssueAgentTokenResponse,
  type CaRootResponse,
} from "@/api/agents";
import { apiRequest, normalizeApiError } from "@/lib/bimemo-api";
import { QUERY_KEYS, errorToDisplayText } from "@/hooks/use-server-state";

export function AgentConnectionPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();

  // ── CA root ─────────────────────────────────────────────────────
  // A 503 is the page's one documented special case: the server has no CA
  // root configured at all. Every other failure goes through the closed
  // error mapping.
  const {
    data: caData,
    isInitialLoading: caLoading,
    error: caQueryError,
  } = useQuery({
    queryKey: QUERY_KEYS.caRoot,
    queryFn: () => apiRequest<CaRootResponse>("/admin/agents/ca-root"),
  });
  const caCert = caData?.certificate ?? null;
  const normalizedCa = caQueryError ? normalizeApiError(caQueryError) : null;
  const caError =
    caQueryError === null
      ? null
      : normalizedCa !== null &&
          normalizedCa.kind === "http" &&
          normalizedCa.status === 503
        ? t("admin.caRootNotConfigured")
        : errorToDisplayText(caQueryError, t);

  // ── Token list ──────────────────────────────────────────────────
  const {
    data: tokenEntries,
    isInitialLoading: tokensLoading,
    error: tokensQueryError,
  } = useQuery({
    queryKey: QUERY_KEYS.agentTokens,
    queryFn: listAgentTokens,
  });
  const tokens = tokenEntries ?? [];
  const tokensError = tokensQueryError ? errorToDisplayText(tokensQueryError, t) : null;

  // ── Issue form ──────────────────────────────────────────────────
  const [showIssue, setShowIssue] = useState(false);
  const [issueLabel, setIssueLabel] = useState("");
  const [issueCapability, setIssueCapability] = useState("read");
  const [issuing, setIssuing] = useState(false);
  const [issuedToken, setIssuedToken] = useState<IssueAgentTokenResponse | null>(null);
  const [pendingIssue, setPendingIssue] = useState<{
    label: string;
    capability: string;
  } | null>(null);
  const [copied, setCopied] = useState(false);

  // ── Revoke ──────────────────────────────────────────────────────
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  const [showReauth, setShowReauth] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);

  // ── Global error ────────────────────────────────────────────────
  const [error, setError] = useState<string | null>(null);

  const issueMutation = useMutation({
    mutationFn: ({
      label,
      capability,
      reauthToken,
    }: {
      label: string;
      capability: string;
      reauthToken: string;
    }) => issueAgentToken(label, capability, reauthToken),
    onSuccess: (result) => {
      setIssuedToken(result);
      setIssueLabel("");
      setShowIssue(false);
      void queryClient.invalidateQueries({ queryKey: QUERY_KEYS.agentTokens });
    },
    onSettled: () => setIssuing(false),
    onError: (err) => {
      const normalized = normalizeApiError(err);
      if (normalized.kind === "http" && normalized.status === 403) {
        setError(t("admin.reauthRequired"));
      } else {
        setError(errorToDisplayText(err, t));
      }
    },
  });

  const revokeMutation = useMutation({
    mutationFn: ({ label, reauthToken }: { label: string; reauthToken: string }) =>
      revokeAgentToken(label, reauthToken),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: QUERY_KEYS.agentTokens });
    },
    onSettled: () => {
      setDeleteTarget(null);
      setPendingDelete(null);
    },
    onError: (err) => {
      const normalized = normalizeApiError(err);
      if (normalized.kind === "http" && normalized.status === 403) {
        setError(t("admin.reauthRequired"));
      } else {
        setError(errorToDisplayText(err, t));
      }
    },
  });

  async function handleDownloadCa() {
    if (!caCert) return;
    const blob = new Blob([caCert], { type: "application/x-x509-ca-cert" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "bimemo-ca-root.crt";
    a.click();
    URL.revokeObjectURL(url);
  }

  function handleIssue(e: React.FormEvent) {
    e.preventDefault();
    if (!issueLabel.trim()) return;
    setError(null);
    // Issuing an access credential requires re-authentication, like revoke.
    setPendingIssue({ label: issueLabel, capability: issueCapability });
    setShowReauth(true);
  }

  // The MCP endpoint an external agent uses is the operator-facing host over
  // TLS (Caddy, port 443) — not the internal container address the backend
  // sees behind the dev proxy. Build it from the browser's own hostname.
  function mcpUrl() {
    return `https://${window.location.hostname}/mcp`;
  }

  // Standard MCP HTTP-client config (the format Claude Code / Cursor use, and
  // that pi-mcp-adapter imports). `token` is the bearer secret, or a
  // placeholder when we no longer hold it.
  function agentConfigJson(token: string) {
    return JSON.stringify(
      {
        mcpServers: {
          bimemo: {
            url: mcpUrl(),
            headers: { Authorization: `Bearer ${token}` },
          },
        },
      },
      null,
      2
    );
  }

  async function copyText(text: string) {
    await navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  function handleDeleteClick(label: string) {
    setPendingDelete(label);
    setShowReauth(true);
  }

  function handleReauthSuccess(reauthToken?: string) {
    setShowReauth(false);
    if (!reauthToken) {
      setPendingDelete(null);
      setPendingIssue(null);
      return;
    }

    if (pendingIssue) {
      const { label, capability } = pendingIssue;
      setPendingIssue(null);
      setIssuing(true);
      setError(null);
      issueMutation.mutate({ label, capability, reauthToken });
      return;
    }

    if (pendingDelete) {
      setDeleteTarget(pendingDelete);
      revokeMutation.mutate({ label: pendingDelete, reauthToken });
    }
  }

  return (
    <AdminLayout title={t("admin.agentConnection")}>
      <div className="flex-1 overflow-y-auto p-6 space-y-8 max-w-3xl">
        <ErrorAlert error={error ?? ""} />

        {/* ── 1. CA Download & Trust ───────────────────────────── */}
        <section className="space-y-3">
          <div className="flex items-center gap-2">
            <Shield className="h-5 w-5 text-muted-foreground" />
            <h2 className="text-lg font-semibold">{t("admin.trustCaTitle")}</h2>
          </div>
          <p className="text-sm text-muted-foreground">
            {t("admin.trustCaDescription")}
          </p>
          {caLoading ? (
            <p className="text-sm text-muted-foreground">{t("admin.loadingCaCert")}</p>
          ) : caError ? (
            <p className="text-sm text-destructive">{caError}</p>
          ) : caCert ? (
            <Button size="sm" onClick={handleDownloadCa}>
              <Download className="mr-1 h-4 w-4" />
              {t("admin.downloadCaRoot")}
            </Button>
          ) : null}
        </section>

        <hr />

        {/* ── 2. Issue / List / Revoke ─────────────────────────── */}
        <section className="space-y-3">
          <div className="flex items-center gap-2">
            <Key className="h-5 w-5 text-muted-foreground" />
            <h2 className="text-lg font-semibold">{t("admin.manageTokensTitle")}</h2>
          </div>

          {/* Issued token display */}
          {issuedToken && (
            <div className="rounded-lg border border-green-200 bg-green-50 p-4 space-y-2">
              <p className="text-sm font-medium text-green-800">
                {t("admin.tokenIssuedFor", { label: issuedToken.label })}
              </p>
              <div className="flex items-center gap-2">
                <code className="flex-1 font-mono text-sm font-medium text-green-900 bg-white border border-green-300 rounded px-2 py-1 break-all select-all">
                  {issuedToken.token}
                </code>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    navigator.clipboard.writeText(issuedToken.token);
                    setCopied(true);
                    setTimeout(() => setCopied(false), 2000);
                  }}
                >
                  {copied ? (
                    <Check className="h-4 w-4" />
                  ) : (
                    <Copy className="h-4 w-4" />
                  )}
                </Button>
              </div>
              <p className="text-xs text-green-600">{t("admin.tokenShownOnce")}</p>

              <div className="pt-1 space-y-1">
                <p className="text-xs font-medium text-green-800">
                  {t("admin.readyConfigLabel")}
                </p>
                <pre className="text-xs text-green-900 bg-white border border-green-300 rounded p-2 overflow-x-auto">
                  {agentConfigJson(issuedToken.token)}
                </pre>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => copyText(agentConfigJson(issuedToken.token))}
                >
                  <Copy className="mr-1 h-4 w-4" /> {t("admin.copyConfig")}
                </Button>
              </div>
            </div>
          )}

          {/* Issue form */}
          <div className="flex justify-end">
            <Button size="sm" onClick={() => setShowIssue(!showIssue)}>
              <Plus className="mr-1 h-4 w-4" />
              {t("admin.issueToken")}
            </Button>
          </div>

          {showIssue && (
            <form
              onSubmit={handleIssue}
              className="space-y-3 rounded-lg border border-border p-4"
            >
              <Input
                placeholder={t("admin.tokenLabelPlaceholder")}
                value={issueLabel}
                onChange={(e) => setIssueLabel(e.target.value)}
                required
              />
              <select
                value={issueCapability}
                onChange={(e) => setIssueCapability(e.target.value)}
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
              >
                <option value="read">{t("admin.readOption")}</option>
                <option value="read-write">{t("admin.readWriteOption")}</option>
              </select>
              <Button type="submit" disabled={issuing} size="sm">
                {issuing ? t("admin.issuing") : t("admin.issue")}
              </Button>
            </form>
          )}

          {/* Token list */}
          {tokensLoading ? (
            <AdminCardSkeleton count={3} />
          ) : tokensError ? (
            <p className="text-sm text-destructive">{tokensError}</p>
          ) : tokens.length === 0 ? (
            <p className="text-muted-foreground">{t("admin.noTokensConfigured")}</p>
          ) : (
            <div className="space-y-2">
              {tokens.map((entry) => (
                <div
                  key={entry.label}
                  className="flex items-center justify-between rounded-lg border border-border p-4"
                >
                  <div>
                    <p className="font-medium">{entry.label}</p>
                    <p className="text-sm text-muted-foreground">
                      {entry.capability} · {entry.created_at}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() => handleDeleteClick(entry.label)}
                      disabled={deleteTarget === entry.label}
                      aria-label={t("admin.revokeNamed", { name: entry.label })}
                    >
                      <Trash2 className="h-4 w-4 text-destructive" />
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>

        <hr />

        {/* ── 3. Connection Config Block ───────────────────────── */}
        <section className="space-y-3">
          <div className="flex items-center gap-2">
            <Settings className="h-5 w-5 text-muted-foreground" />
            <h2 className="text-lg font-semibold">
              {t("admin.connectionConfigTitle")}
            </h2>
          </div>
          <p className="text-sm text-muted-foreground">
            {t("admin.connectAgentIntro")}
          </p>
          <ol className="list-decimal pl-5 text-sm text-muted-foreground space-y-1">
            <li>{t("admin.connectStep1")}</li>
            <li>{t("admin.connectStep2")}</li>
            <li>
              {t("admin.connectStep3Before")} <code>~/.config/mcp/mcp.json</code>{" "}
              {t("admin.connectStep3After")}
            </li>
            <li>{t("admin.connectStep4")}</li>
          </ol>

          <div className="rounded-lg border border-border p-4 space-y-2">
            <p className="text-xs font-medium">{t("admin.configTemplateLabel")}</p>
            <pre className="text-sm bg-muted p-3 rounded overflow-x-auto">
              {agentConfigJson("<paste-your-token>")}
            </pre>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => copyText(agentConfigJson("<paste-your-token>"))}
            >
              {copied ? (
                <>
                  <Check className="mr-1 h-4 w-4" /> {t("admin.copied")}
                </>
              ) : (
                <>
                  <Copy className="mr-1 h-4 w-4" /> {t("admin.copyTemplate")}
                </>
              )}
            </Button>
            <p className="text-xs text-muted-foreground">
              {t("admin.endpointLabel")} <code>{mcpUrl()}</code>{" "}
              {t("admin.endpointNote")}
            </p>
          </div>
        </section>

        {/* Reauth modal */}
        {showReauth && (
          <ReauthModal
            onSuccess={handleReauthSuccess}
            onClose={() => {
              setShowReauth(false);
              setPendingDelete(null);
              setPendingIssue(null);
            }}
          />
        )}
      </div>
    </AdminLayout>
  );
}
