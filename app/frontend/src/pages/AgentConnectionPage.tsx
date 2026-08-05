import { useState, useEffect } from "react";
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
  type AgentTokenEntry,
  type IssueAgentTokenResponse,
  type CaRootResponse,
} from "@/api/agents";
import { apiRequest, ApiError } from "@/lib/bimemo-api";

export function AgentConnectionPage() {
  // ── CA root download ────────────────────────────────────────────
  const [caCert, setCaCert] = useState<string | null>(null);
  const [caLoading, setCaLoading] = useState(false);
  const [caError, setCaError] = useState<string | null>(null);

  // ── Token list ──────────────────────────────────────────────────
  const [tokens, setTokens] = useState<AgentTokenEntry[]>([]);
  const [tokensLoading, setTokensLoading] = useState(true);
  const [tokensError, setTokensError] = useState<string | null>(null);

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

  // Load tokens and the CA cert once, on mount.
  useEffect(() => {
    loadTokens();
    loadCaRoot();
  }, []);

  async function loadTokens() {
    setTokensLoading(true);
    setTokensError(null);
    try {
      const data = await listAgentTokens();
      setTokens(data);
    } catch (err) {
      setTokensError(err instanceof Error ? err.message : "Failed to load tokens");
    } finally {
      setTokensLoading(false);
    }
  }

  async function loadCaRoot() {
    setCaLoading(true);
    setCaError(null);
    try {
      const data: CaRootResponse = await apiRequest("/admin/agents/ca-root");
      setCaCert(data.certificate);
    } catch (err) {
      if (err instanceof ApiError && err.status === 503) {
        setCaError("CA root certificate is not configured on the server.");
      } else {
        setCaError(err instanceof Error ? err.message : "Failed to load CA root");
      }
    } finally {
      setCaLoading(false);
    }
  }

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

  async function handleReauthSuccess(reauthToken?: string) {
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
      try {
        const result = await issueAgentToken(label, capability, reauthToken);
        setIssuedToken(result);
        setIssueLabel("");
        setShowIssue(false);
        await loadTokens();
      } catch (err) {
        if (err instanceof ApiError && err.status === 403) {
          setError("Re-authentication required");
        } else {
          setError(err instanceof Error ? err.message : "Failed to issue token");
        }
      } finally {
        setIssuing(false);
      }
      return;
    }

    if (pendingDelete) {
      setDeleteTarget(pendingDelete);
      try {
        await revokeAgentToken(pendingDelete, reauthToken);
        await loadTokens();
      } catch (err) {
        if (err instanceof ApiError && err.status === 403) {
          setError("Re-authentication required");
        } else {
          setError(err instanceof Error ? err.message : "Failed to revoke");
        }
      } finally {
        setDeleteTarget(null);
        setPendingDelete(null);
      }
    }
  }

  return (
    <AdminLayout title="Agent Connection">
      <div className="flex-1 overflow-y-auto p-6 space-y-8 max-w-3xl">
        <ErrorAlert error={error ?? ""} />

        {/* ── 1. CA Download & Trust ───────────────────────────── */}
        <section className="space-y-3">
          <div className="flex items-center gap-2">
            <Shield className="h-5 w-5 text-muted-foreground" />
            <h2 className="text-lg font-semibold">1. Trust the CA Certificate</h2>
          </div>
          <p className="text-sm text-muted-foreground">
            Download the CA root certificate and install it in your system's trusted
            certificate store so your agent can connect over TLS without trust errors.
          </p>
          {caLoading ? (
            <p className="text-sm text-muted-foreground">Loading CA cert…</p>
          ) : caError ? (
            <p className="text-sm text-destructive">{caError}</p>
          ) : caCert ? (
            <Button size="sm" onClick={handleDownloadCa}>
              <Download className="mr-1 h-4 w-4" />
              Download CA Root
            </Button>
          ) : null}
        </section>

        <hr />

        {/* ── 2. Issue / List / Revoke ─────────────────────────── */}
        <section className="space-y-3">
          <div className="flex items-center gap-2">
            <Key className="h-5 w-5 text-muted-foreground" />
            <h2 className="text-lg font-semibold">2. Manage Agent Tokens</h2>
          </div>

          {/* Issued token display */}
          {issuedToken && (
            <div className="rounded-lg border border-green-200 bg-green-50 p-4 space-y-2">
              <p className="text-sm font-medium text-green-800">
                Token issued for "{issuedToken.label}"
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
              <p className="text-xs text-green-600">
                ⚠ This is the only time the token is shown. Save it securely.
              </p>

              <div className="pt-1 space-y-1">
                <p className="text-xs font-medium text-green-800">
                  Ready-to-paste agent config (token included):
                </p>
                <pre className="text-xs text-green-900 bg-white border border-green-300 rounded p-2 overflow-x-auto">
                  {agentConfigJson(issuedToken.token)}
                </pre>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => copyText(agentConfigJson(issuedToken.token))}
                >
                  <Copy className="mr-1 h-4 w-4" /> Copy config
                </Button>
              </div>
            </div>
          )}

          {/* Issue form */}
          <div className="flex justify-end">
            <Button size="sm" onClick={() => setShowIssue(!showIssue)}>
              <Plus className="mr-1 h-4 w-4" />
              Issue Token
            </Button>
          </div>

          {showIssue && (
            <form
              onSubmit={handleIssue}
              className="space-y-3 rounded-lg border border-border p-4"
            >
              <Input
                placeholder="Label (e.g., claude)"
                value={issueLabel}
                onChange={(e) => setIssueLabel(e.target.value)}
                required
              />
              <select
                value={issueCapability}
                onChange={(e) => setIssueCapability(e.target.value)}
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
              >
                <option value="read">Read</option>
                <option value="read-write">Read-Write</option>
              </select>
              <Button type="submit" disabled={issuing} size="sm">
                {issuing ? "Issuing..." : "Issue"}
              </Button>
            </form>
          )}

          {/* Token list */}
          {tokensLoading ? (
            <AdminCardSkeleton count={3} />
          ) : tokensError ? (
            <p className="text-sm text-destructive">{tokensError}</p>
          ) : tokens.length === 0 ? (
            <p className="text-muted-foreground">No agent tokens configured.</p>
          ) : (
            <div className="space-y-2">
              {tokens.map((t) => (
                <div
                  key={t.label}
                  className="flex items-center justify-between rounded-lg border border-border p-4"
                >
                  <div>
                    <p className="font-medium">{t.label}</p>
                    <p className="text-sm text-muted-foreground">
                      {t.capability} · {t.created_at}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() => handleDeleteClick(t.label)}
                      disabled={deleteTarget === t.label}
                      aria-label={`Revoke ${t.label}`}
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
            <h2 className="text-lg font-semibold">3. Connection Configuration</h2>
          </div>
          <p className="text-sm text-muted-foreground">
            To connect an external agent (e.g. the pi agent via pi-mcp-adapter):
          </p>
          <ol className="list-decimal pl-5 text-sm text-muted-foreground space-y-1">
            <li>
              On the agent's machine, install the CA root from step 1 into its trusted
              certificate store.
            </li>
            <li>
              Issue a token in step 2 and copy its ready-to-paste config (the token is
              shown only once).
            </li>
            <li>
              Save that config as <code>~/.config/mcp/mcp.json</code> on the agent's
              machine (pi reads it from there).
            </li>
            <li>Start the agent — it can now read and write biMEMO memory over MCP.</li>
          </ol>

          <div className="rounded-lg border border-border p-4 space-y-2">
            <p className="text-xs font-medium">
              Config template (paste your token from step 2):
            </p>
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
                  <Check className="mr-1 h-4 w-4" /> Copied
                </>
              ) : (
                <>
                  <Copy className="mr-1 h-4 w-4" /> Copy template
                </>
              )}
            </Button>
            <p className="text-xs text-muted-foreground">
              Endpoint: <code>{mcpUrl()}</code> — this reflects the address you opened
              biMEMO with. Open the panel by biMEMO's network address if the agent runs
              on another machine.
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
