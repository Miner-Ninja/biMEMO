import { useMemo, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { Settings, Database, Cpu, User, Bug, Sparkles, MessageSquare, Shield, ShieldCheck, CalendarClock } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useAuth } from '@/hooks/useAuth'
import { UserMenu } from './UserMenu'
import { ChatSearch } from './ChatSearch'
import {
  getSidebarActionButtonClassName,
  getSidebarItemClassName,
  SidebarPanel,
  SidebarSectionLabel,
  SidebarShell,
} from './sidebar-chrome'

const adminLinks = [
  { to: '/admin', icon: Shield, label: 'Dashboard' },
  { to: '/admin/profile', icon: User, label: 'User Profile' },
  { to: '/admin/scheduled-tasks', icon: CalendarClock, label: 'Scheduled Tasks' },
  { to: '/admin/skills', icon: Sparkles, label: 'Skills' },
  { to: '/admin/memory-debug', icon: Bug, label: 'Memory Debug' },
  { to: '/admin/integrations', icon: Settings, label: 'Integrations' },
  { to: '/admin/backups', icon: Database, label: 'Backups' },
  { to: '/admin/llm', icon: Cpu, label: 'LLM Configuration' },
  { to: '/settings/security', icon: ShieldCheck, label: 'Security' },
]

/**
 * Admin sidebar with navigation links, active state, and back-to-chat link.
 */
export function AdminSidebar() {
  const { pathname } = useLocation()
  const { logout } = useAuth()
  const [searchQuery, setSearchQuery] = useState('')

  const isActive = (to: string): boolean => {
    if (to === '/admin') return pathname === '/admin'
    return pathname.startsWith(to)
  }

  const filteredLinks = useMemo(() => {
    const normalizedQuery = searchQuery.trim().toLowerCase()
    if (!normalizedQuery) return adminLinks
    return adminLinks.filter((link) => link.label.toLowerCase().includes(normalizedQuery))
  }, [searchQuery])

  return (
    <SidebarShell
      icon={Shield}
      title="biMEMO"
      subtitle="Administration"
      toolbar={
        <SidebarPanel className="space-y-3 p-3">
          <SidebarSectionLabel>Navigation</SidebarSectionLabel>
          <ChatSearch
            value={searchQuery}
            onChange={setSearchQuery}
            placeholder="Search settings..."
          />
        </SidebarPanel>
      }
      content={
        <SidebarPanel className="h-full overflow-hidden p-2">
          <nav className="flex h-full min-h-0 flex-col gap-1 overflow-y-auto">
            {filteredLinks.length > 0 ? filteredLinks.map((link) => {
              const active = isActive(link.to)
              return (
                <Link
                  key={link.to}
                  to={link.to}
                  className={cn(getSidebarItemClassName(active), active && 'font-medium')}
                >
                  <link.icon className="h-4 w-4 shrink-0" />
                  {link.label}
                </Link>
              )
            }) : (
              <div className="px-3 py-4 text-sm text-sidebar-foreground/55">
                No settings found for &ldquo;{searchQuery}&rdquo;
              </div>
            )}
          </nav>
        </SidebarPanel>
      }
      footer={
        <div className="flex flex-col gap-2">
          <Link
            to="/"
            className={getSidebarActionButtonClassName()}
          >
            <MessageSquare className="h-4 w-4 shrink-0" />
            Back to Chat
          </Link>
          <UserMenu onLogout={logout} />
        </div>
      }
    />
  )
}
