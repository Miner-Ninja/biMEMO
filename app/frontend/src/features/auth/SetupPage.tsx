import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useAuth } from "@/features/auth/hooks/useAuth";
import { authLogin, authSetup, waitForApplicationReady } from "@/services/bimemo-api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ErrorAlert } from "@/components/ui/error-alert";

export function SetupPage() {
  const { t } = useTranslation();
  const { setAuthenticated } = useAuth();
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [startupMessage, setStartupMessage] = useState("");

  const passwordTooShort = password.length > 0 && password.length < 8;
  const passwordsMatch = password === confirm;
  const canSubmit = password.length >= 8 && passwordsMatch && !loading;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;

    setLoading(true);
    setError("");
    setStartupMessage("");

    try {
      const result = await authSetup(password);
      if (result.status === "starting") {
        setStartupMessage(t("auth.startingServices"));
        await waitForApplicationReady();
        await authLogin(password);
      }
      setAuthenticated(true);
    } catch (err: unknown) {
      const apiErr = err as { status?: number; message?: string };
      if (apiErr.status === 409) {
        setError(t("auth.passwordAlreadyConfigured"));
      } else {
        setError(apiErr.message || t("auth.setupFailed"));
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
            {t("auth.createPasswordToStart")}
          </p>
          <p className="mt-3 text-xs text-muted-foreground">
            {t("auth.setupPasswordNote")}
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
              disabled={loading}
            />
            {passwordTooShort && (
              <p className="text-xs text-muted-foreground">
                {t("auth.minCharacters", { count: 8 - password.length })}
              </p>
            )}
            {password.length >= 8 && (
              <p className="text-xs text-green-600">{t("auth.passwordLengthOk")}</p>
            )}
          </div>

          <div className="space-y-1.5">
            <label htmlFor="confirm" className="text-sm font-medium">
              {t("auth.confirmPassword")}
            </label>
            <Input
              id="confirm"
              type="password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              disabled={loading}
            />
            {confirm.length > 0 && !passwordsMatch && (
              <p className="text-xs text-destructive">
                {t("auth.passwordsDoNotMatch")}
              </p>
            )}
          </div>

          <ErrorAlert error={error} />
          {startupMessage && (
            <p className="text-sm text-muted-foreground">{startupMessage}</p>
          )}

          <Button type="submit" className="w-full" disabled={!canSubmit}>
            {loading ? t("auth.settingUp") : t("auth.createPassword")}
          </Button>
        </form>
      </div>
    </div>
  );
}
