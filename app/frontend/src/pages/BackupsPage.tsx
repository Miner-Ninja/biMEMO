import { useTranslation } from "react-i18next";
import { Database } from "lucide-react";
import { AdminLayout } from "@/components/layout/AdminLayout";

export function BackupsPage() {
  const { t } = useTranslation();
  return (
    <AdminLayout title={t("admin.backups")}>
      <div className="flex-1 overflow-y-auto p-6 max-w-3xl">
        <div className="rounded-xl border border-border bg-card p-6">
          <div className="flex items-start gap-4">
            <div className="rounded-lg bg-primary/10 p-3 text-primary">
              <Database className="h-5 w-5" />
            </div>
            <div className="space-y-3">
              <div>
                <h2 className="font-semibold">{t("admin.backupsUnavailableTitle")}</h2>
                <p className="mt-1 text-sm text-muted-foreground">
                  {t("admin.backupsUnavailableBody")}
                </p>
              </div>
              <p className="text-sm text-muted-foreground">
                {t("admin.backupsImplementationNote")}
              </p>
            </div>
          </div>
        </div>
      </div>
    </AdminLayout>
  );
}
