import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Shield, MessageSquare, FileText, FolderTree } from "lucide-react";
import { NewChatButton } from "./NewChatButton";
import { ChatList } from "./ChatList";
import { ChatSearch } from "./ChatSearch";
import { FileList } from "./FileList";
import { WorkspaceFileTree } from "./WorkspaceFileTree";
import { UserMenu } from "./UserMenu";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type { Chat } from "@/types/chat";
import { SidebarPanel, SidebarShell } from "./sidebar-chrome";

type TabKey = "chats" | "files" | "workspace";

interface SidebarProps {
  chats: Chat[];
  bridgeProvidersByChatId?: Record<string, string>;
  activeChatId: string | null;
  onNewChat: () => void;
  onSelectChat: (chatId: string) => void;
  onDeleteChat: (chatId: string) => void;
  onRenameChat: (chatId: string, newTitle: string) => void;
  username?: string | null;
  onLogout?: () => void;
  activeFiles: string[];
  onActiveFilesChange: (fileIds: string[]) => void;
  onFilesChange?: () => void;
  fileRefreshKey?: number;
  voiceMode?: boolean;
  onToggleVoiceMode?: (() => void) | undefined;
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
  const { t } = useTranslation();
  const [searchQuery, setSearchQuery] = useState("");
  const [activeTab, setActiveTab] = useState<TabKey>("chats");

  const switchTab = (tab: TabKey) => {
    setActiveTab(tab);
    setSearchQuery("");
  };

  return (
    <SidebarShell
      icon={Shield}
      title="biMEMO"
      subtitle={t("sidebar.workspace")}
      toolbar={
        <SidebarPanel className="space-y-3 p-3">
          <Tabs value={activeTab} onValueChange={(value) => switchTab(value as TabKey)}>
            <TabsList className="grid h-auto w-full grid-cols-3 gap-2 border-sidebar-border bg-sidebar-accent/30 p-1">
              <TabsTrigger
                value="chats"
                className="gap-2 rounded-md py-2 text-sm data-[state=active]:bg-sidebar-accent data-[state=active]:text-sidebar-accent-foreground"
              >
                <MessageSquare className="h-4 w-4" />
                {t("sidebar.chats")}
              </TabsTrigger>
              <TabsTrigger
                value="files"
                className="gap-2 rounded-md py-2 text-sm data-[state=active]:bg-sidebar-accent data-[state=active]:text-sidebar-accent-foreground"
              >
                <FileText className="h-4 w-4" />
                {t("sidebar.files")}
                {activeFiles.length > 0 && ` (${activeFiles.length})`}
              </TabsTrigger>
              <TabsTrigger
                value="workspace"
                className="gap-2 rounded-md py-2 text-sm data-[state=active]:bg-sidebar-accent data-[state=active]:text-sidebar-accent-foreground"
              >
                <FolderTree className="h-4 w-4" />
                {t("sidebar.workspace")}
              </TabsTrigger>
            </TabsList>
          </Tabs>

          {activeTab !== "workspace" && (
            <ChatSearch
              value={searchQuery}
              onChange={setSearchQuery}
              placeholder={
                activeTab === "chats"
                  ? t("sidebar.searchChats")
                  : t("sidebar.searchFiles")
              }
            />
          )}
        </SidebarPanel>
      }
      content={
        <div className="h-full min-h-0 overflow-hidden">
          {activeTab === "chats" ? (
            <ChatList
              chats={chats}
              bridgeProvidersByChatId={bridgeProvidersByChatId}
              activeChatId={activeChatId}
              searchQuery={searchQuery}
              onSelectChat={onSelectChat}
              onDeleteChat={onDeleteChat}
              onRenameChat={onRenameChat}
            />
          ) : activeTab === "files" ? (
            <FileList
              activeFiles={activeFiles}
              onActiveFilesChange={onActiveFilesChange}
              onFilesChange={onFilesChange}
              refreshKey={fileRefreshKey}
              searchQuery={searchQuery}
            />
          ) : (
            <WorkspaceFileTree />
          )}
        </div>
      }
      footer={
        <div className="flex flex-col gap-2">
          <NewChatButton onClick={onNewChat} />
          <UserMenu
            userName={username || t("sidebar.user")}
            onLogout={onLogout}
            voiceMode={voiceMode}
            onToggleVoiceMode={onToggleVoiceMode}
          />
        </div>
      }
    />
  );
}
