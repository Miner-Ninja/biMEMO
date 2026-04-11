import { useState } from 'react'
import { Shield, MessageSquare, FileText } from 'lucide-react'
import { NewChatButton } from './NewChatButton'
import { ChatList } from './ChatList'
import { ChatSearch } from './ChatSearch'
import { FileList } from './FileList'
import { UserMenu } from './UserMenu'
import type { Chat } from '@/types/chat'
import { cn } from '@/lib/utils'
import {
  getSidebarItemClassName,
  SidebarFooterPanel,
  SidebarPanel,
  SidebarShell,
} from './sidebar-chrome'

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
    <SidebarShell
      icon={Shield}
      title="biMEMO"
      subtitle="Workspace"
      toolbar={
        <SidebarPanel className="space-y-3 p-3">
          <div className="grid grid-cols-2 gap-2">
            <button
              type="button"
              onClick={() => switchTab('chats')}
              className={cn(getSidebarItemClassName(activeTab === 'chats'), 'justify-center')}
            >
              <MessageSquare className="h-4 w-4" />
              Chats
            </button>
            <button
              type="button"
              onClick={() => switchTab('files')}
              className={cn(getSidebarItemClassName(activeTab === 'files'), 'justify-center')}
            >
              <FileText className="h-4 w-4" />
              Files{activeFiles.length > 0 && ` (${activeFiles.length})`}
            </button>
          </div>

          <ChatSearch
            value={searchQuery}
            onChange={setSearchQuery}
            placeholder={activeTab === 'chats' ? 'Search chats...' : 'Search files...'}
          />
        </SidebarPanel>
      }
      content={
        <SidebarPanel className="h-full min-h-0 overflow-hidden">
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
            <FileList
              activeFiles={activeFiles}
              onActiveFilesChange={onActiveFilesChange}
              onFilesChange={onFilesChange}
              refreshKey={fileRefreshKey}
              searchQuery={searchQuery}
            />
          )}
        </SidebarPanel>
      }
      footer={
        <SidebarFooterPanel>
          <NewChatButton onClick={onNewChat} />
          <UserMenu
            userName={username || 'User'}
            onLogout={onLogout}
            voiceMode={voiceMode}
            onToggleVoiceMode={onToggleVoiceMode}
          />
        </SidebarFooterPanel>
      }
    />
  )
}
