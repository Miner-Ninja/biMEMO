import { useEffect, type ReactNode } from "react";
import { Routes, Route, Navigate, useNavigate } from "react-router-dom";
import { ThemeProvider } from "@/components/layout/ThemeProvider";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { AuthContext, useAuthState, useAuth } from "@/hooks/useAuth";
import { syncUiSettings } from "@/lib/ui-settings";
import { applyStoredLanguage } from "@/i18n";
import { AppSkeleton } from "@/components/ui/skeletons";
import { ChatPage } from "@/pages/ChatPage";
import { LoginPage } from "@/pages/LoginPage";
import { SetupPage } from "@/pages/SetupPage";
import { AdminDashboard } from "@/pages/AdminDashboard";
import { IntegrationsPage } from "@/pages/IntegrationsPage";
import { AgentConnectionPage } from "@/pages/AgentConnectionPage";
import { BackupsPage } from "@/pages/BackupsPage";
import { LLMConfigPage } from "@/pages/admin/LLMConfigPage";
import { MemoryDebugPage } from "@/pages/admin/MemoryDebugPage";
import { ProfilePage } from "@/pages/admin/ProfilePage";
import { ScheduledTasksPage } from "@/pages/admin/ScheduledTasksPage";
import { SkillsPage } from "@/pages/admin/SkillsPage";
import { SkillDetailPage } from "@/pages/admin/SkillDetailPage";

function AuthProvider({ children }: { children: ReactNode }) {
  const authState = useAuthState();
  return <AuthContext.Provider value={authState}>{children}</AuthContext.Provider>;
}

function AppRoutes() {
  const { authenticated, loading, checkStatus } = useAuth();

  useEffect(() => {
    if (authenticated) {
      void syncUiSettings().then(() => {
        applyStoredLanguage();
      });
    }
  }, [authenticated]);
  const navigate = useNavigate();

  useEffect(() => {
    let cancelled = false;

    void checkStatus()
      .then((status) => {
        if (!cancelled && status.needs_setup) {
          navigate("/setup", { replace: true });
        }
      })
      .catch((error) => {
        console.error("Initial auth check failed:", error);
      });

    return () => {
      cancelled = true;
    };
  }, [checkStatus, navigate]);

  if (loading) {
    return <AppSkeleton />;
  }

  if (!authenticated) {
    return (
      <Routes>
        <Route path="/setup" element={<SetupPage />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    );
  }

  return (
    <Routes>
      <Route path="/" element={<ChatPage />} />
      <Route path="/c/:conversationId" element={<ChatPage />} />
      <Route path="/admin" element={<AdminDashboard />} />
      <Route path="/admin/integrations" element={<IntegrationsPage />} />
      <Route path="/admin/agents" element={<AgentConnectionPage />} />
      <Route path="/admin/backups" element={<BackupsPage />} />
      <Route path="/admin/llm" element={<LLMConfigPage />} />
      <Route path="/admin/profile" element={<ProfilePage />} />
      <Route path="/admin/scheduled-tasks" element={<ScheduledTasksPage />} />
      <Route path="/admin/memory-debug" element={<MemoryDebugPage />} />
      <Route path="/admin/skills" element={<SkillsPage />} />
      <Route path="/admin/skills/:name" element={<SkillDetailPage />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

function App() {
  return (
    <ErrorBoundary>
      <ThemeProvider>
        <AuthProvider>
          <AppRoutes />
        </AuthProvider>
      </ThemeProvider>
    </ErrorBoundary>
  );
}

export default App;
