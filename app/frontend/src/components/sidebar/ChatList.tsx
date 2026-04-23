import { useMemo } from 'react'
import { ScrollArea } from '@/components/ui/scroll-area'
import { ChatItem } from './ChatItem'
import { groupChatsByDate, GROUP_ORDER } from '@/lib/chat-groups'
import type { Chat } from '@/types/chat'
import { SidebarSectionLabel } from './sidebar-chrome'

interface ChatListProps {
  chats: Chat[]
  bridgeProvidersByChatId: Record<string, string>
  activeChatId: string | null
  searchQuery: string
  onSelectChat: (chatId: string) => void
  onDeleteChat: (chatId: string) => void
  onRenameChat: (chatId: string, newTitle: string) => void
}

/**
 * Check if a chat matches the search query
 */
function matchesSearch(chat: Chat, query: string): boolean {
  if (!query) return true
  const q = query.toLowerCase()
  if (chat.title.toLowerCase().includes(q)) return true
  return chat.messages.some((m) => m.content.toLowerCase().includes(q))
}

/**
 * Grouped list of chats in the sidebar with search filtering
 */
export function ChatList({
  chats,
  bridgeProvidersByChatId,
  activeChatId,
  searchQuery,
  onSelectChat,
  onDeleteChat,
  onRenameChat,
}: ChatListProps) {
  // Filter chats based on search query
  const filteredChats = useMemo(
    () => chats.filter((chat) => matchesSearch(chat, searchQuery)),
    [chats, searchQuery]
  )

  const groups = groupChatsByDate(filteredChats)

  if (chats.length === 0) {
    return (
      <div className="flex h-full items-center justify-center px-4 text-center text-sm text-sidebar-foreground/55">
        No chats yet.
        <br />
        Start a new chat!
      </div>
    )
  }

  if (filteredChats.length === 0 && searchQuery) {
    return (
      <div className="flex h-full items-center justify-center px-4 text-center text-sm text-sidebar-foreground/55">
        No results for &ldquo;{searchQuery}&rdquo;
      </div>
    )
  }

  return (
    <ScrollArea className="h-full">
      <div className="flex flex-col gap-4 py-2">
        {GROUP_ORDER.map((groupName) => {
          const groupChats = groups.get(groupName)
          if (!groupChats || groupChats.length === 0) return null

          return (
            <div key={groupName} className="flex flex-col gap-1">
              <SidebarSectionLabel className="px-3">{groupName}</SidebarSectionLabel>
              <div>
                {groupChats.map((chat) => (
                  <ChatItem
                    key={chat.id}
                    chat={chat}
                    bridgeProvider={bridgeProvidersByChatId[chat.id]}
                    isActive={chat.id === activeChatId}
                    onSelect={() => onSelectChat(chat.id)}
                    onDelete={() => onDeleteChat(chat.id)}
                    onRename={(newTitle) => onRenameChat(chat.id, newTitle)}
                  />
                ))}
              </div>
            </div>
          )
        })}
      </div>
    </ScrollArea>
  )
}
