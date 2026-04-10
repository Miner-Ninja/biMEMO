import { useState } from 'react'
import { Shield, MessageSquare, FileText } from 'lucide-react'
import { Separator } from '@/components/ui/separator'
import { NewChatButton } from './NewChatButton'
import { ChatList } from './ChatList'
import { ChatSearch } from './ChatSearch'
import { FileList } from './FileList'
import { UserMenu } from './UserMenu'
import type { Chat } from '@/types/chat'

interface SidebarProps {
  chats: Chat[]
  bridgeProvidersByChatId?: Record<string, string>
  activeChatId: string | null
  onNewChat: () => void
  onSelectChat: (chatId: string) => void
  onDeleteChat: (chatId: string) => void
  onRenameChat: (chatId: string, newTitle: string) => void
  username?: string | null
  onLogout?: () => void
  activeFiles: string[]
  onActiveFilesChange: (fileIds: string[]) => void
  onFilesChange?: () => void
  fileRefreshKey?: number
  voiceMode?: boolean
  onToggleVoiceMode?: () => void
}

/**
 * Application sidebar with chat history, file management, and search
 */
export function Sidebar({
  chats,
  bridgeProvidersByChatId = {},
  activeChatId,
  onNewChat,
  onSelectChat,
  onDeleteChat,
  onRenameChat,
  username,
  onLogout,
  activeFiles,
  onActiveFilesChange,
  onFilesChange,
  fileRefreshKey,
  voiceMode,
  onToggleVoiceMode,
}: SidebarProps) {
  const [searchQuery, setSearchQuery] = useState('')
  const [activeTab, setActiveTab] = useState<'chats' | 'files'>('chats')

  const switchTab = (tab: 'chats' | 'files') => {
    setActiveTab(tab)
    setSearchQuery('')
  }

  return (
    <div className="flex h-full flex-col">
      {/* Header - fixed */}
      <div className="shrink-0">
        <div className="flex h-14 items-center gap-2 px-4">
          <Shield className="h-5 w-5 text-primary" />
          <span className="font-semibold">biMEMO</span>
        </div>
        <Separator />

        {/* Tabs */}
        <div className="flex px-2 pt-2 gap-1">
          <button
            onClick={() => switchTab('chats')}
            className={`flex flex-1 items-center justify-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
              activeTab === 'chats'
                ? 'bg-background border border-input text-foreground'
                : 'text-muted-foreground hover:text-foreground hover:bg-accent/50'
            }`}
          >
            <MessageSquare className="h-4 w-4" />
            Chats
          </button>
          <button
            onClick={() => switchTab('files')}
            className={`flex flex-1 items-center justify-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
              activeTab === 'files'
                ? 'bg-background border border-input text-foreground'
                : 'text-muted-foreground hover:text-foreground hover:bg-accent/50'
            }`}
          >
            <FileText className="h-4 w-4" />
            Files{activeFiles.length > 0 && ` (${activeFiles.length})`}
          </button>
        </div>

        {/* Search — works for both chats and files */}
        <div className="py-2">
          <ChatSearch
            value={searchQuery}
            onChange={setSearchQuery}
            placeholder={activeTab === 'chats' ? 'Search chats...' : 'Search files...'}
          />
        </div>
      </div>

      {/* Content - scrollable */}
      <div className="flex-1 min-h-0 overflow-hidden">
        {activeTab === 'chats' ? (
          <ChatList
            chats={chats}
            bridgeProvidersByChatId={bridgeProvidersByChatId}
            activeChatId={activeChatId}
            searchQuery={searchQuery}
            onSelectChat={onSelectChat}
            onDeleteChat={onDeleteChat}
            onRenameChat={onRenameChat}
          />
        ) : (
          <FileList activeFiles={activeFiles} onActiveFilesChange={onActiveFilesChange} onFilesChange={onFilesChange} refreshKey={fileRefreshKey} searchQuery={searchQuery} />
        )}
      </div>

      {/* Footer - fixed */}
      <div className="shrink-0 flex flex-col gap-2 px-2 pb-4 pt-2">
        <NewChatButton onClick={onNewChat} />
        <UserMenu userName={username || 'User'} onLogout={onLogout} voiceMode={voiceMode} onToggleVoiceMode={onToggleVoiceMode} />
      </div>
    </div>
  )
}
