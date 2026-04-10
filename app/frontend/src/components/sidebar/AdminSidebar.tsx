import { Link, useLocation } from 'react-router-dom'
import { Settings, Database, Cpu, User, Bug, Sparkles, MessageSquare, Shield, CalendarClock } from 'lucide-react'
import { Separator } from '@/components/ui/separator'
import { cn } from '@/lib/utils'

const adminLinks = [
  { to: '/admin', icon: Shield, label: 'Dashboard' },
  { to: '/admin/profile', icon: User, label: 'User Profile' },
  { to: '/admin/scheduled-tasks', icon: CalendarClock, label: 'Scheduled Tasks' },
  { to: '/admin/skills', icon: Sparkles, label: 'Skills' },
  { to: '/admin/memory-debug', icon: Bug, label: 'Memory Debug' },
  { to: '/admin/integrations', icon: Settings, label: 'Integrations' },
  { to: '/admin/backups', icon: Database, label: 'Backups' },
  { to: '/admin/llm', icon: Cpu, label: 'LLM Configuration' },
]

/**
 * Admin sidebar with navigation links, active state, and back-to-chat link.
 */
export function AdminSidebar() {
  const { pathname } = useLocation()

  const isActive = (to: string): boolean => {
    if (to === '/admin') return pathname === '/admin'
    return pathname.startsWith(to)
  }

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="shrink-0">
        <div className="flex h-14 items-center gap-2 px-4">
          <Shield className="h-5 w-5 text-primary" />
          <span className="font-semibold">App Settings</span>
        </div>
        <Separator />
      </div>

      {/* Navigation links - scrollable */}
      <nav className="flex-1 min-h-0 overflow-y-auto px-2 py-2 space-y-0.5">
        {adminLinks.map((link) => {
          const active = isActive(link.to)
          return (
            <Link
              key={link.to}
              to={link.to}
              className={cn(
                'flex items-center gap-2.5 rounded-md px-3 py-2 text-sm transition-colors',
                active
                  ? 'bg-background border border-input text-foreground font-medium'
                  : 'text-muted-foreground hover:text-foreground hover:bg-accent/50'
              )}
            >
              <link.icon className="h-4 w-4 shrink-0" />
              {link.label}
            </Link>
          )
        })}
      </nav>

      {/* Footer - back to chat */}
      <div className="shrink-0 px-2 pb-4 pt-2">
        <Link
          to="/"
          className="flex h-11 w-full items-center gap-2 rounded-md border border-input bg-background px-3 py-2 text-sm font-medium shadow-xs hover:bg-accent hover:text-accent-foreground transition-colors"
        >
          <MessageSquare className="h-4 w-4 shrink-0" />
          Back to Chat
        </Link>
      </div>
    </div>
  )
}
