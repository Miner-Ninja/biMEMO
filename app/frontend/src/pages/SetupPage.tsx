import { useState } from "react";
import { useAuth } from "@/hooks/useAuth";
import { authLogin, authSetup, waitForApplicationReady } from "@/lib/bimemo-api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ErrorAlert } from "@/components/ui/error-alert";

export function SetupPage() {
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
        setStartupMessage("Starting encrypted services. This can take a few seconds.");
        await waitForApplicationReady();
        await authLogin(password);
      }
      setAuthenticated(true);
    } catch (err: unknown) {
      const apiErr = err as { status?: number; message?: string };
      if (apiErr.status === 409) {
        setError("Password already configured.");
      } else {
        setError(apiErr.message || "Failed to set up password");
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
            Create a password to get started
          </p>
          <p className="mt-3 text-xs text-muted-foreground">
            This same password unlocks the encrypted database and signs you in. To
            enable automatic server startup later, store this password in the local
            Docker secret file; that file is plaintext and must stay private.
          </p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-1.5">
            <label htmlFor="password" className="text-sm font-medium">
              Password
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
                Minimum 8 characters ({8 - password.length} more needed)
              </p>
            )}
            {password.length >= 8 && (
              <p className="text-xs text-green-600">Password length OK</p>
            )}
          </div>

          <div className="space-y-1.5">
            <label htmlFor="confirm" className="text-sm font-medium">
              Confirm Password
            </label>
            <Input
              id="confirm"
              type="password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              disabled={loading}
            />
            {confirm.length > 0 && !passwordsMatch && (
              <p className="text-xs text-destructive">Passwords do not match</p>
            )}
          </div>

          <ErrorAlert error={error} />
          {startupMessage && (
            <p className="text-sm text-muted-foreground">{startupMessage}</p>
          )}

          <Button type="submit" className="w-full" disabled={!canSubmit}>
            {loading ? "Setting up..." : "Create Password"}
          </Button>
        </form>
      </div>
    </div>
  );
}
