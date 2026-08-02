import { Link } from "react-router-dom";
import { Settings, Database, Cpu, User, Bug, Sparkles } from "lucide-react";
import { AdminLayout } from "@/components/layout/AdminLayout";

const adminLinks = [
  {
    to: "/admin/llm",
    icon: Cpu,
    title: "LLM Configuration",
    description: "Configure AI provider, model, and credentials",
  },
  {
    to: "/admin/profile",
    icon: User,
    title: "User Profile",
    description: "Set your name, timezone, language, and instructions",
  },
  {
    to: "/admin/integrations",
    icon: Settings,
    title: "Integrations",
    description: "Manage external service connections",
  },
  {
    to: "/admin/backups",
    icon: Database,
    title: "Backups",
    description: "Temporarily unavailable in the active backend stack",
  },
  {
    to: "/admin/memory-debug",
    icon: Bug,
    title: "Memory Debug",
    description: "Inspect memory pipeline, context, timeline, and live events",
  },
  {
    to: "/admin/skills",
    icon: Sparkles,
    title: "Skills",
    description: "View, edit, and manage agent learned skills",
  },
];

export function AdminDashboard() {
  return (
    <AdminLayout title="Dashboard">
      <div className="flex-1 overflow-y-auto p-6">
        <p className="text-muted-foreground mb-6">Manage your biMEMO instance</p>
        <div className="grid content-start gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {adminLinks.map((link) => (
            <Link
              key={link.to}
              to={link.to}
              className="flex flex-col gap-2 rounded-lg border border-border p-4 hover:bg-muted/50 transition-colors"
            >
              <link.icon className="h-6 w-6 text-primary" />
              <h2 className="font-semibold">{link.title}</h2>
              <p className="text-sm text-muted-foreground">{link.description}</p>
            </Link>
          ))}
        </div>
      </div>
    </AdminLayout>
  );
}
