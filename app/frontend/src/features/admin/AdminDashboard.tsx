import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import {
  Settings,
  Database,
  Cpu,
  User,
  Bug,
  Brain,
  Sparkles,
  Activity,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { TranslationKey } from "@/i18n";
import { AdminLayout } from "@/components/layout/AdminLayout";

const adminLinks: Array<{
  to: string;
  icon: LucideIcon;
  titleKey: TranslationKey;
  descriptionKey: TranslationKey;
}> = [
  {
    to: "/admin/llm",
    icon: Cpu,
    titleKey: "admin.llmConfiguration",
    descriptionKey: "admin.llmConfigDescription",
  },
  {
    to: "/admin/profile",
    icon: User,
    titleKey: "admin.userProfile",
    descriptionKey: "admin.profileDescription",
  },
  {
    to: "/admin/memory",
    icon: Brain,
    titleKey: "memory.title",
    descriptionKey: "memory.dashboardDescription",
  },
  {
    to: "/admin/integrations",
    icon: Settings,
    titleKey: "admin.integrations",
    descriptionKey: "admin.integrationsDescription",
  },
  {
    to: "/admin/backups",
    icon: Database,
    titleKey: "admin.backups",
    descriptionKey: "admin.backupsDescription",
  },
  {
    to: "/admin/memory-debug",
    icon: Bug,
    titleKey: "admin.memoryDebug",
    descriptionKey: "admin.memoryDebugDescription",
  },
  {
    to: "/admin/skills",
    icon: Sparkles,
    titleKey: "admin.skills",
    descriptionKey: "admin.skillsDescription",
  },
  {
    to: "/admin/trace",
    icon: Activity,
    titleKey: "admin.turnTrace",
    descriptionKey: "admin.turnTraceDescription",
  },
];

export function AdminDashboard() {
  const { t } = useTranslation();
  return (
    <AdminLayout title={t("admin.dashboard")}>
      <div className="flex-1 overflow-y-auto p-6">
        <p className="text-muted-foreground mb-6">{t("admin.dashboardSubtitle")}</p>
        <div className="grid content-start gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {adminLinks.map((link) => (
            <Link
              key={link.to}
              to={link.to}
              className="flex flex-col gap-2 rounded-lg border border-border p-4 hover:bg-muted/50 transition-colors"
            >
              <link.icon className="h-6 w-6 text-primary" />
              <h2 className="font-semibold">{t(link.titleKey)}</h2>
              <p className="text-sm text-muted-foreground">{t(link.descriptionKey)}</p>
            </Link>
          ))}
        </div>
      </div>
    </AdminLayout>
  );
}
