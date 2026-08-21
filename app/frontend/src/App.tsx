import { useEffect, type ReactNode } from "react";
import { Routes, Route, Navigate, useNavigate } from "react-router-dom";
import { QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider } from "@/components/layout/ThemeProvider";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { AuthContext, useAuthState, useAuth } from "@/features/auth/hooks/useAuth";
import { syncUiSettings } from "@/utils/ui-settings";
import { queryClient } from "@/services/query-client";
import { applyStoredLanguage } from "@/i18n";
import { AppSkeleton } from "@/components/ui/skeletons";
import { ChatPage } from "@/features/chat/ChatPage";
import { LoginPage } from "@/features/auth/LoginPage";
import { SetupPage } from "@/features/auth/SetupPage";
import { AdminDashboard } from "@/features/admin/AdminDashboard";
import { IntegrationsPage } from "@/features/integrations/IntegrationsPage";
import { AgentConnectionPage } from "@/features/integrations/AgentConnectionPage";
import { BackupsPage } from "@/features/backups/BackupsPage";
import { LLMConfigPage } from "@/features/llm-config/LLMConfigPage";
import { MemoryDebugPage } from "@/features/memory/MemoryDebugPage";
import { TracePage } from "@/features/traces/TracePage";
import { MemoryPage } from "@/features/memory/MemoryPage";
import { EntityDetailPage } from "@/features/memory/EntityDetailPage";
import { ProfilePage } from "@/features/profile/ProfilePage";
import { ScheduledTasksPage } from "@/features/scheduler/ScheduledTasksPage";
import { SkillsPage } from "@/features/skills/SkillsPage";
import { SkillDetailPage } from "@/features/skills/SkillDetailPage";

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
      <Route path="/admin/memory" element={<MemoryPage />} />
      <Route path="/admin/memory-debug" element={<MemoryDebugPage />} />
      <Route path="/admin/trace" element={<TracePage />} />
      <Route
        path="/admin/memory-debug/entity/:entityId"
        element={<EntityDetailPage />}
      />
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
          <QueryClientProvider client={queryClient}>
            <AppRoutes />
          </QueryClientProvider>
        </AuthProvider>
      </ThemeProvider>
    </ErrorBoundary>
  );
}

export default App;
