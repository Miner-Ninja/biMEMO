import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useAuth } from "@/features/auth/hooks/useAuth";
import { authLogin, waitForApplicationReady } from "@/services/bimemo-api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ErrorAlert } from "@/components/ui/error-alert";

export function LoginPage() {
  const { t } = useTranslation();
  const { setAuthenticated } = useAuth();
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [retryAfter, setRetryAfter] = useState(0);
  const [startupMessage, setStartupMessage] = useState("");

  useEffect(() => {
    if (retryAfter <= 0) return;

    const timer = window.setTimeout(() => {
      setRetryAfter((prev) => {
        const next = Math.max(0, prev - 1);
        setError(next > 0 ? t("auth.tooManyAttempts", { seconds: next }) : "");
        return next;
      });
    }, 1000);

    return () => window.clearTimeout(timer);
  }, [retryAfter, t]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError("");
    setStartupMessage("");

    try {
      const result = await authLogin(password);
      if (result.status === "starting") {
        setStartupMessage(t("auth.unlockAccepted"));
        await waitForApplicationReady();
        await authLogin(password);
      }
      setAuthenticated(true);
    } catch (err: unknown) {
      const apiErr = err as {
        status?: number;
        retryAfter?: string;
        message?: string;
      };
      if (apiErr.status === 429) {
        const parsed = parseInt(apiErr.retryAfter || "30", 10);
        const seconds = Number.isFinite(parsed) && parsed > 0 ? parsed : 30;
        setRetryAfter(seconds);
        setError(t("auth.tooManyAttempts", { seconds }));
      } else {
        setError(apiErr.message || t("auth.invalidPassword"));
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-background">
      <div className="w-full max-w-sm space-y-6 p-8">
        <div className="text-center">
          <h1 className="text-2xl font-bold">biMEMO</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            {t("auth.enterPasswordToContinue")}
          </p>
          <p className="mt-3 text-xs text-muted-foreground">
            {t("auth.manualUnlockNote")}
          </p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-1.5">
            <label htmlFor="password" className="text-sm font-medium">
              {t("auth.password")}
            </label>
            <Input
              id="password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoFocus
              disabled={loading || retryAfter > 0}
            />
          </div>

          <ErrorAlert error={error} />
          {startupMessage && (
            <p className="text-sm text-muted-foreground">{startupMessage}</p>
          )}

          <Button
            type="submit"
            className="w-full"
            disabled={loading || !password || retryAfter > 0}
          >
            {loading ? t("auth.signingIn") : t("auth.signIn")}
          </Button>
        </form>
      </div>
    </div>
  );
}
