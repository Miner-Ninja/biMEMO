import { useMemo, useState } from "react";
import type { TranslationKey } from "@/i18n";
import { useTranslation } from "react-i18next";
import { Link, useLocation } from "react-router-dom";
import {
  Plug,
  Settings,
  Database,
  Cpu,
  User,
  Bug,
  Brain,
  Sparkles,
  MessageSquare,
  Shield,
  CalendarClock,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import { useAuth } from "@/hooks/useAuth";
import { UserMenu } from "./UserMenu";
import { ChatSearch } from "./ChatSearch";
import {
  getSidebarActionButtonClassName,
  getSidebarItemClassName,
  SidebarPanel,
  SidebarSectionLabel,
  SidebarShell,
} from "./sidebar-chrome";

const adminLinks: Array<{ to: string; icon: LucideIcon; labelKey: TranslationKey }> = [
  { to: "/admin", icon: Shield, labelKey: "admin.dashboard" },
  { to: "/admin/profile", icon: User, labelKey: "admin.userProfile" },
  { to: "/admin/memory", icon: Brain, labelKey: "memory.title" },
  {
    to: "/admin/scheduled-tasks",
    icon: CalendarClock,
    labelKey: "scheduler.scheduledTasksTitle",
  },
  { to: "/admin/skills", icon: Sparkles, labelKey: "admin.skills" },
  { to: "/admin/memory-debug", icon: Bug, labelKey: "admin.memoryDebug" },
  { to: "/admin/integrations", icon: Settings, labelKey: "admin.integrations" },
  { to: "/admin/agents", icon: Plug, labelKey: "admin.agentConnection" },
  { to: "/admin/backups", icon: Database, labelKey: "admin.backups" },
  { to: "/admin/llm", icon: Cpu, labelKey: "admin.llmConfiguration" },
];

/**
 * Admin sidebar with navigation links, active state, and back-to-chat link.
 */
export function AdminSidebar() {
  const { t } = useTranslation();
  const { pathname } = useLocation();
  const { logout } = useAuth();
  const [searchQuery, setSearchQuery] = useState("");

  const isActive = (to: string): boolean => {
    if (to === "/admin") return pathname === "/admin";
    return pathname.startsWith(to);
  };

  const filteredLinks = useMemo(() => {
    const normalizedQuery = searchQuery.trim().toLowerCase();
    if (!normalizedQuery) return adminLinks;
    return adminLinks.filter((link) =>
      t(link.labelKey).toLowerCase().includes(normalizedQuery)
    );
  }, [searchQuery, t]);

  return (
    <SidebarShell
      icon={Shield}
      title="biMEMO"
      subtitle={t("admin.administration")}
      toolbar={
        <SidebarPanel className="space-y-3 p-3">
          <SidebarSectionLabel>{t("admin.navigation")}</SidebarSectionLabel>
          <ChatSearch
            value={searchQuery}
            onChange={setSearchQuery}
            placeholder={t("admin.searchSettingsPlaceholder")}
          />
        </SidebarPanel>
      }
      content={
        <SidebarPanel className="h-full overflow-hidden p-2">
          <nav className="flex h-full min-h-0 flex-col gap-1 overflow-y-auto">
            {filteredLinks.length > 0 ? (
              filteredLinks.map((link) => {
                const active = isActive(link.to);
                return (
                  <Link
                    key={link.to}
                    to={link.to}
                    className={cn(
                      getSidebarItemClassName(active),
                      active && "font-medium"
                    )}
                  >
                    <link.icon className="h-4 w-4 shrink-0" />
                    {t(link.labelKey)}
                  </Link>
                );
              })
            ) : (
              <div className="px-3 py-4 text-sm text-sidebar-foreground/55">
                {t("admin.noSettingsFound", { query: searchQuery })}
              </div>
            )}
          </nav>
        </SidebarPanel>
      }
      footer={
        <div className="flex flex-col gap-2">
          <Link to="/" className={getSidebarActionButtonClassName()}>
            <MessageSquare className="h-4 w-4 shrink-0" />
            {t("admin.backToChat")}
          </Link>
          <UserMenu onLogout={logout} />
        </div>
      }
    />
  );
}
