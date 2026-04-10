import { useLocation, useNavigate } from 'react-router-dom'
import { User, LogOut, Settings, Shield, AudioLines } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubTrigger,
  DropdownMenuSubContent,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar'
import { cn } from '@/lib/utils'

interface UserMenuProps {
  userName?: string
  onLogout?: () => void
  voiceMode?: boolean
  onToggleVoiceMode?: () => void
}

/**
 * User menu with avatar and dropdown actions
 */
export function UserMenu({ userName = 'User', onLogout, voiceMode, onToggleVoiceMode }: UserMenuProps) {
  const location = useLocation()
  const navigate = useNavigate()
  const isAdmin = location.pathname.startsWith('/admin')
  const initials = userName
    .split(' ')
    .map((n) => n[0])
    .join('')
    .toUpperCase()
    .slice(0, 2)

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="outline"
          className="h-11 w-full justify-start gap-2"
        >
          <Avatar className="h-8 w-8">
            <AvatarImage src="/avatars/user" alt="User avatar" className="object-cover" />
            <AvatarFallback className="bg-primary text-primary-foreground text-xs">
              {initials}
            </AvatarFallback>
          </Avatar>
          <span className="truncate text-sm">{userName}</span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-48">
        <DropdownMenuItem disabled>
          <User className="mr-2 h-4 w-4" />
          {userName}
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem onClick={() => navigate(isAdmin ? '/' : '/admin')}>
          <Shield className="mr-2 h-4 w-4" />
          {isAdmin ? 'Back to Chat' : 'App Settings'}
        </DropdownMenuItem>
        {onToggleVoiceMode && (
          <DropdownMenuSub>
            <DropdownMenuSubTrigger>
              <Settings className="mr-2 h-4 w-4" />
              Settings
            </DropdownMenuSubTrigger>
            <DropdownMenuSubContent>
              <DropdownMenuItem
                onSelect={(e) => {
                  e.preventDefault()
                  onToggleVoiceMode()
                }}
              >
                <AudioLines className="mr-2 h-4 w-4" />
                <span className="flex-1">Voice Mode</span>
                <span
                  className={cn(
                    'relative inline-flex h-4 w-7 shrink-0 rounded-full border-2 border-transparent transition-colors',
                    voiceMode ? 'bg-primary' : 'bg-muted'
                  )}
                >
                  <span
                    className={cn(
                      'pointer-events-none inline-block h-3 w-3 rounded-full bg-background shadow-sm transition-transform',
                      voiceMode ? 'translate-x-3' : 'translate-x-0'
                    )}
                  />
                </span>
              </DropdownMenuItem>
            </DropdownMenuSubContent>
          </DropdownMenuSub>
        )}
        <DropdownMenuSeparator />
        <DropdownMenuItem className="text-destructive" onClick={onLogout}>
          <LogOut className="mr-2 h-4 w-4" />
          Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
