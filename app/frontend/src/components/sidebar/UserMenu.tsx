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
import { Switch } from '@/components/ui/switch'
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar'
import { getSidebarActionButtonClassName } from './sidebar-chrome'

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
          className={getSidebarActionButtonClassName()}
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
                <Switch
                  checked={Boolean(voiceMode)}
                  className="pointer-events-none ml-auto scale-75"
                  aria-label="Voice mode"
                />
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
