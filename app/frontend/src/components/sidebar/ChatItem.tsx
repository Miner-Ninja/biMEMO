import { useState, useRef, useEffect } from 'react'
import { MessageSquare, MoreVertical, Pencil, Trash2 } from 'lucide-react'
import { BridgeProviderIcon } from '@/components/icons/BridgeProviderIcon'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import type { Chat } from '@/types/chat'
import { cn, truncate } from '@/lib/utils'

interface ChatItemProps {
  chat: Chat
  bridgeProvider?: string
  isActive: boolean
  onSelect: () => void
  onDelete: () => void
  onRename: (newTitle: string) => void
}

/**
 * Single chat entry in the sidebar with rename and delete options
 */
export function ChatItem({
  chat,
  bridgeProvider,
  isActive,
  onSelect,
  onDelete,
  onRename,
}: ChatItemProps) {
  const [isEditing, setIsEditing] = useState(false)
  const [editValue, setEditValue] = useState(chat.title)
  const inputRef = useRef<HTMLInputElement>(null)

  // Focus input when entering edit mode
  useEffect(() => {
    if (isEditing && inputRef.current) {
      inputRef.current.focus()
      inputRef.current.select()
    }
  }, [isEditing])

  const handleStartEdit = () => {
    setEditValue(chat.title)
    setIsEditing(true)
  }

  const handleSaveEdit = () => {
    const trimmed = editValue.trim()
    if (trimmed && trimmed !== chat.title) {
      onRename(trimmed)
    }
    setIsEditing(false)
  }

  const handleCancelEdit = () => {
    setEditValue(chat.title)
    setIsEditing(false)
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      handleSaveEdit()
    } else if (e.key === 'Escape') {
      handleCancelEdit()
    }
  }

  const handleDelete = (e: React.MouseEvent) => {
    e.stopPropagation()
    onDelete()
  }

  const handleItemKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      if (!isEditing) {
        onSelect()
      }
    }
  }

  const bridgeTitle = bridgeProvider
    ? `${bridgeProvider.charAt(0).toUpperCase() + bridgeProvider.slice(1)} bridge connected`
    : ''

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={isEditing ? undefined : onSelect}
      onKeyDown={isEditing ? undefined : handleItemKeyDown}
      className={cn(
        'group relative flex w-full items-center gap-2 px-3 py-2 text-left text-sm',
        'hover:bg-sidebar-accent focus-visible:bg-sidebar-accent focus-visible:outline-none',
        'min-h-[44px]',
        isEditing ? 'cursor-default' : 'cursor-pointer',
        isActive && 'bg-sidebar-accent'
      )}
    >
      {/* Chat icon and title */}
      <MessageSquare className="h-4 w-4 shrink-0 text-muted-foreground" />

      {isEditing ? (
        <Input
          ref={inputRef}
          type="text"
          value={editValue}
          onChange={(e) => setEditValue(e.target.value)}
          onBlur={handleSaveEdit}
          onKeyDown={handleKeyDown}
          onClick={(e) => e.stopPropagation()}
          className="h-6 flex-1 px-1 text-sm"
        />
      ) : (
        <div className="flex min-w-0 flex-1 items-center gap-1.5">
          <span className="truncate text-left">{truncate(chat.title, 30)}</span>
          {bridgeProvider ? (
            <span
              className="inline-flex shrink-0 items-center text-primary"
              title={bridgeTitle}
            >
              <BridgeProviderIcon provider={bridgeProvider} />
            </span>
          ) : null}
        </div>
      )}

      {/* Fade gradient - always visible */}
      {!isEditing && (
        <div className="pointer-events-none absolute right-0 top-0 bottom-0 z-10 w-20 bg-gradient-to-r from-transparent to-sidebar group-hover:to-sidebar-accent" />
      )}

      {/* Actions menu - visible on hover */}
      {!isEditing && (
        <div
          className="absolute -right-1 top-0 bottom-0 z-20 flex items-center opacity-0 group-hover:opacity-100"
          onClick={(e) => e.stopPropagation()}
        >
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7"
                aria-label="Chat actions"
              >
                <MoreVertical className="h-4 w-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-40">
              <DropdownMenuItem onClick={handleStartEdit}>
                <Pencil className="mr-2 h-4 w-4" />
                Rename
              </DropdownMenuItem>
              <DropdownMenuItem
                onClick={handleDelete}
                className="text-destructive focus:text-destructive"
              >
                <Trash2 className="mr-2 h-4 w-4" />
                Delete
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      )}
    </div>
  )
}
