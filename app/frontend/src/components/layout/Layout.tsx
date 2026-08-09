import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { PanelLeft } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";

interface LayoutProps {
  sidebar?: ReactNode;
  header?: ReactNode;
  children: ReactNode;
}

/**
 * Main layout with sidebar and main content area
 */
export function Layout({ sidebar, header, children }: LayoutProps) {
  const { t } = useTranslation();
  const [sidebarOpen, setSidebarOpen] = useState(false);

  const toggleSidebar = () => setSidebarOpen((prev) => !prev);

  return (
    <div className="flex h-dvh min-h-0 w-full overflow-hidden bg-background">
      {sidebar && (
        <>
          <Sheet open={sidebarOpen} onOpenChange={setSidebarOpen}>
            <SheetContent
              side="left"
              className="w-[85vw] max-w-80 border-sidebar-border p-0 md:hidden"
            >
              <SheetHeader className="sr-only">
                <SheetTitle>{t("layout.navigationSidebar")}</SheetTitle>
                <SheetDescription>
                  {t("layout.navigationSidebarDescription")}
                </SheetDescription>
              </SheetHeader>
              {sidebar}
            </SheetContent>
          </Sheet>

          <aside className="hidden w-[85vw] max-w-80 flex-col border-r border-sidebar-border bg-sidebar text-sidebar-foreground md:relative md:flex">
            {sidebar}
          </aside>
        </>
      )}

      <main className="flex flex-1 flex-col overflow-hidden">
        {header && (
          <div className="flex shrink-0 items-center border-b border-border">
            <Button
              type="button"
              variant="ghost"
              size="icon"
              onClick={toggleSidebar}
              className="ml-1 size-10 shrink-0 md:hidden"
              aria-label={t("layout.toggleSidebar")}
            >
              <PanelLeft data-icon="inline-start" />
            </Button>
            <div className="flex-1">{header}</div>
          </div>
        )}

        <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden">
          {children}
        </div>
      </main>
    </div>
  );
}
