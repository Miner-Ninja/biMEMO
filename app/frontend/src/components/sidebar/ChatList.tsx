import { useMemo } from 'react'
import { ScrollArea } from '@/components/ui/scroll-area'
import { ChatItem } from './ChatItem'
import { groupChatsByDate, GROUP_ORDER } from '@/lib/chat-groups'
import type { Chat } from '@/types/chat'

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
      <div className="flex-1 px-3 py-4 text-center text-sm text-muted-foreground">
        No chats yet.
        <br />
        Start a new chat!
      </div>
    )
  }

  if (filteredChats.length === 0 && searchQuery) {
    return (
      <div className="flex-1 px-3 pt-16 text-center text-sm text-muted-foreground">
        No results for &ldquo;{searchQuery}&rdquo;
      </div>
    )
  }

  return (
    <ScrollArea className="h-full">
      <div className="space-y-4 pt-2">
        {GROUP_ORDER.map((groupName) => {
          const groupChats = groups.get(groupName)
          if (!groupChats || groupChats.length === 0) return null

          return (
            <div key={groupName}>
              <h3 className="mb-1 px-3 text-xs font-medium text-muted-foreground/70">
                {groupName}
              </h3>
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
