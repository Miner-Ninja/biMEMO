import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { apiRequest } from "@/services/bimemo-api";

interface ReauthModalProps {
  onSuccess: (token?: string) => void;
  onClose: () => void;
}

export function ReauthModal({ onSuccess, onClose }: ReauthModalProps) {
  const { t } = useTranslation();
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
      setError(err instanceof Error ? err.message : t("auth.authenticationFailed"));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
      <div className="w-full max-w-sm rounded-lg border border-border bg-background p-6 shadow-lg space-y-4">
        <div>
          <h2 className="text-lg font-semibold">{t("auth.reauthRequired")}</h2>
          <p className="text-sm text-muted-foreground">{t("auth.reauthDescription")}</p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-3">
          <Input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder={t("auth.enterYourPassword")}
            autoFocus
            autoComplete="current-password"
            required
          />

          {error && <p className="text-sm text-destructive">{error}</p>}

          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={onClose}>
              {t("common.cancel")}
            </Button>
            <Button type="submit" disabled={!password || loading}>
              {loading ? t("auth.verifying") : t("common.confirm")}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
