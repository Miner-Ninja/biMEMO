import { useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { AdminLayout } from "@/components/layout/AdminLayout";
import { AdminCardSkeleton } from "@/components/ui/skeletons";
import { Input } from "@/components/ui/input";
import { ErrorAlert } from "@/components/ui/error-alert";
import { ReauthModal } from "@/components/ReauthModal";
import { useAdminData } from "@/hooks/useAdminData";
import { apiRequest, ApiError, encodePathSegment } from "@/lib/bimemo-api";

interface Integration {
  name: string;
  type: string;
}

export function IntegrationsPage() {
  const {
    data: integrations,
    loading,
    error,
    setError,
    refetch,
  } = useAdminData<Integration[]>("/admin/integrations");
  const [showAdd, setShowAdd] = useState(false);
  const [name, setName] = useState("");
  const [type, setType] = useState("");
  const [config, setConfig] = useState("");
  const [adding, setAdding] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  const [showReauth, setShowReauth] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);

  const handleAdd = async (e: React.FormEvent) => {
    e.preventDefault();
    setAdding(true);
    try {
      let parsedConfig: unknown;
      if (!config.trim()) {
        parsedConfig = {};
      } else {
        try {
          parsedConfig = JSON.parse(config);
        } catch {
          throw new Error("Config JSON must be valid JSON");
        }
      }

      await apiRequest("/admin/integrations", {
        method: "POST",
        body: JSON.stringify({ name, type, config: parsedConfig }),
      });
      setShowAdd(false);
      setName("");
      setType("");
      setConfig("");
      await refetch();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to add integration");
    } finally {
      setAdding(false);
    }
  };

  const handleDeleteClick = (name: string) => {
    setPendingDelete(name);
    setShowReauth(true);
  };

  const handleReauthSuccess = async (reauthToken?: string) => {
    setShowReauth(false);
    if (!pendingDelete || !reauthToken) return;
    setDeleteTarget(pendingDelete);
    try {
      await apiRequest(`/admin/integrations/${encodePathSegment(pendingDelete)}`, {
        method: "DELETE",
        headers: { "X-Reauth-Token": reauthToken },
      });
      await refetch();
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) {
        setError("Re-authentication required");
      } else {
        setError(err instanceof Error ? err.message : "Failed to delete");
      }
    } finally {
      setDeleteTarget(null);
      setPendingDelete(null);
    }
  };

  return (
    <AdminLayout title="Integrations">
      <div className="flex-1 overflow-y-auto p-6 space-y-6 max-w-3xl">
        <ErrorAlert error={error} />

        <div className="flex justify-end">
          <Button size="sm" onClick={() => setShowAdd(!showAdd)}>
            <Plus className="mr-1 h-4 w-4" />
            Add Integration
          </Button>
        </div>

        {showAdd && (
          <form
            onSubmit={handleAdd}
            className="space-y-3 rounded-lg border border-border p-4"
          >
            <Input
              placeholder="Integration name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
            />
            <Input
              placeholder="Type (e.g., telegram, email)"
              value={type}
              onChange={(e) => setType(e.target.value)}
              required
            />
            <Input
              placeholder='Config JSON (optional, e.g., {"token": "..."})'
              value={config}
              onChange={(e) => setConfig(e.target.value)}
            />
            <Button type="submit" disabled={adding} size="sm">
              {adding ? "Adding..." : "Save"}
            </Button>
          </form>
        )}

        {loading ? (
          <AdminCardSkeleton count={3} />
        ) : !integrations || integrations.length === 0 ? (
          <p className="text-muted-foreground">No integrations configured.</p>
        ) : (
          <div className="space-y-2">
            {integrations.map((intg) => (
              <div
                key={intg.name}
                className="flex items-center justify-between rounded-lg border border-border p-4"
              >
                <div>
                  <p className="font-medium">{intg.name}</p>
                  <p className="text-sm text-muted-foreground">{intg.type}</p>
                </div>
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() => handleDeleteClick(intg.name)}
                  disabled={deleteTarget === intg.name}
                  aria-label={`Delete ${intg.name}`}
                >
                  <Trash2 className="h-4 w-4 text-destructive" />
                </Button>
              </div>
            ))}
          </div>
        )}

        {showReauth && (
          <ReauthModal
            onSuccess={handleReauthSuccess}
            onClose={() => {
              setShowReauth(false);
              setPendingDelete(null);
            }}
          />
        )}
      </div>
    </AdminLayout>
  );
}
