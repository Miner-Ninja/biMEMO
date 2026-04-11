import { useState, type ReactNode } from 'react'
import { PanelLeft } from 'lucide-react'
import { cn } from '@/lib/utils'

interface LayoutProps {
  sidebar?: ReactNode
  header?: ReactNode
  children: ReactNode
}

/**
 * Main layout with sidebar and main content area
 */
export function Layout({ sidebar, header, children }: LayoutProps) {
  const [sidebarOpen, setSidebarOpen] = useState(false)

  const toggleSidebar = () => setSidebarOpen((prev) => !prev)
  const closeSidebar = () => setSidebarOpen(false)

  return (
    <div className="flex h-dvh min-h-0 w-full overflow-hidden bg-background">
      {/* Sidebar - Hidden on mobile by default */}
      {sidebar && (
        <>
          {/* Mobile overlay */}
          {sidebarOpen && (
            <div
              className="fixed inset-0 z-40 bg-black/50 md:hidden"
              onClick={closeSidebar}
              aria-hidden="true"
            />
          )}

          {/* Sidebar container */}
          <aside
            className={cn(
              'fixed inset-y-0 left-0 z-50 flex w-[85vw] max-w-80 flex-col border-r border-sidebar-border bg-sidebar text-sidebar-foreground shadow-xl',
              'transform transition-transform duration-200 ease-in-out',
              'md:relative md:translate-x-0',
              sidebarOpen ? 'translate-x-0' : '-translate-x-full'
            )}
          >
            {sidebar}
          </aside>
        </>
      )}

      {/* Main content area */}
      <main className="flex flex-1 flex-col overflow-hidden">
        {/* Header with sidebar toggle for mobile */}
        {header && (
          <div className="flex shrink-0 items-center border-b border-border">
            {/* Mobile menu button */}
            <button
              type="button"
              className="flex h-12 w-12 shrink-0 items-center justify-center text-muted-foreground transition-colors hover:text-foreground md:hidden"
              onClick={toggleSidebar}
              aria-label="Toggle sidebar"
            >
              <PanelLeft className="h-5 w-5" />
            </button>
            <div className="flex-1">{header}</div>
          </div>
        )}

        {/* Page content */}
        <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden">{children}</div>
      </main>
    </div>
  )
}
