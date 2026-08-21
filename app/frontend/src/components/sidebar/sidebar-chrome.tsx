import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { cn } from "@/utils/utils";

interface SidebarShellProps {
  icon: LucideIcon;
  title: string;
  subtitle: string;
  toolbar?: ReactNode;
  content: ReactNode;
  footer?: ReactNode;
  headerAction?: ReactNode;
}

interface SidebarPanelProps {
  children: ReactNode;
  className?: string;
}

interface SidebarSectionLabelProps {
  children: ReactNode;
  className?: string;
}

const SIDEBAR_PANEL_CLASS_NAME =
  "rounded-xl border border-sidebar-border bg-sidebar-accent/35";

/**
 * Return shared classes for interactive sidebar rows.
 */
export function getSidebarItemClassName(isActive = false): string {
  return cn(
    "flex w-full items-center gap-2.5 rounded-lg border border-transparent px-3 py-2.5 text-sm transition-colors",
    isActive
      ? "border-sidebar-border bg-sidebar-accent text-sidebar-accent-foreground shadow-xs"
      : "text-sidebar-foreground/72 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
  );
}

/**
 * Return shared classes for full-width sidebar action buttons.
 */
export function getSidebarActionButtonClassName(): string {
  return "inline-flex h-11 w-full items-center justify-start gap-2 rounded-lg border border-sidebar-border bg-sidebar-accent/35 px-3 text-sidebar-foreground shadow-none hover:bg-sidebar-accent hover:text-sidebar-accent-foreground";
}

/**
 * Return shared classes for sidebar search inputs.
 */
export function getSidebarSearchInputClassName(): string {
  return "h-9 border-sidebar-border bg-sidebar-accent/30 pl-8 pr-8 text-sm text-sidebar-foreground placeholder:text-sidebar-foreground/45";
}

/**
 * Wrap a logical sidebar block in the shared chrome surface.
 */
export function SidebarPanel({ children, className }: SidebarPanelProps) {
  return <div className={cn(SIDEBAR_PANEL_CLASS_NAME, className)}>{children}</div>;
}

/**
 * Render the shared small uppercase section label used across sidebar blocks.
 */
export function SidebarSectionLabel({ children, className }: SidebarSectionLabelProps) {
  return (
    <div
      className={cn(
        "text-[10px] font-medium uppercase tracking-[0.16em] text-sidebar-foreground/55",
        className
      )}
    >
      {children}
    </div>
  );
}

/**
 * Render the shared chrome used by both app and admin sidebars.
 */
export function SidebarShell({
  icon: Icon,
  title,
  subtitle,
  toolbar,
  content,
  footer,
  headerAction,
}: SidebarShellProps) {
  return (
    <div className="flex h-full flex-col bg-sidebar text-sidebar-foreground">
      <div className="shrink-0 px-2 pt-3">
        <SidebarPanel className="px-3 py-3.5">
          <div className="flex items-center justify-between gap-3">
            <div className="flex min-w-0 items-center gap-3">
              <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-sidebar-border bg-sidebar-primary/10 text-sidebar-primary">
                <Icon className="h-5 w-5" />
              </div>
              <div className="min-w-0">
                <div className="text-sm font-semibold text-sidebar-foreground">
                  {title}
                </div>
                <div className="mt-0.5 text-xs text-sidebar-foreground/55">
                  {subtitle}
                </div>
              </div>
            </div>
            {headerAction ? <div className="shrink-0">{headerAction}</div> : null}
          </div>
        </SidebarPanel>
      </div>

      {toolbar ? <div className="shrink-0 px-2 pt-3">{toolbar}</div> : null}

      <div className="min-h-0 flex-1 px-2 py-3">{content}</div>

      {footer ? <div className="shrink-0 px-2 pb-3">{footer}</div> : null}
    </div>
  );
}
