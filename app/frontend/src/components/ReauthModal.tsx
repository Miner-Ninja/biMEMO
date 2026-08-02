import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { apiRequest } from "@/lib/bimemo-api";

interface ReauthModalProps {
  onSuccess: (token?: string) => void;
  onClose: () => void;
}

export function ReauthModal({ onSuccess, onClose }: ReauthModalProps) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!password || loading) return;
    setError("");
    setLoading(true);

    try {
      const data = await apiRequest<{ reauth_token: string }>("/admin/reauth", {
        method: "POST",
        body: JSON.stringify({ password }),
      });
      onSuccess(data.reauth_token);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Authentication failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
      <div className="w-full max-w-sm rounded-lg border border-border bg-background p-6 shadow-lg space-y-4">
        <div>
          <h2 className="text-lg font-semibold">Re-authentication Required</h2>
          <p className="text-sm text-muted-foreground">
            This action requires password confirmation.
          </p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-3">
          <Input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Enter your password"
            autoFocus
            autoComplete="current-password"
            required
          />

          {error && <p className="text-sm text-destructive">{error}</p>}

          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" disabled={!password || loading}>
              {loading ? "Verifying..." : "Confirm"}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
