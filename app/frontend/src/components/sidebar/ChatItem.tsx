import { useState, useRef, useEffect } from "react";
import { useTranslation } from "react-i18next";
import { MessageSquare, MoreVertical, Pencil, Trash2 } from "lucide-react";
import { BridgeProviderIcon } from "@/components/icons/BridgeProviderIcon";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { Chat } from "@/types/chat";
import { cn, truncate } from "@/lib/utils";
import { getSidebarItemClassName } from "./sidebar-chrome";

interface ChatItemProps {
  chat: Chat;
  bridgeProvider?: string | undefined;
  isActive: boolean;
  onSelect: () => void;
  onDelete: () => void;
  onRename: (newTitle: string) => void;
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
  const { t } = useTranslation();
  const [isEditing, setIsEditing] = useState(false);
  const [editValue, setEditValue] = useState(chat.title);
  const inputRef = useRef<HTMLInputElement>(null);

  // Focus input when entering edit mode
  useEffect(() => {
    if (isEditing && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [isEditing]);

  const handleStartEdit = () => {
    setEditValue(chat.title);
    setIsEditing(true);
  };

  const handleSaveEdit = () => {
    const trimmed = editValue.trim();
    if (trimmed && trimmed !== chat.title) {
      onRename(trimmed);
    }
    setIsEditing(false);
  };

  const handleCancelEdit = () => {
    setEditValue(chat.title);
    setIsEditing(false);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") {
      handleSaveEdit();
    } else if (e.key === "Escape") {
      handleCancelEdit();
    }
  };

  const handleDelete = (e: React.MouseEvent) => {
    e.stopPropagation();
    onDelete();
  };

  const handleItemKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      if (!isEditing) {
        onSelect();
      }
    }
  };

  const bridgeTitle = bridgeProvider
    ? t("sidebar.bridgeConnected", {
        provider: bridgeProvider.charAt(0).toUpperCase() + bridgeProvider.slice(1),
      })
    : "";

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={isEditing ? undefined : onSelect}
      onKeyDown={isEditing ? undefined : handleItemKeyDown}
      className={cn(
        "group relative min-h-[44px] text-left focus-visible:outline-none",
        isEditing ? "cursor-default" : "cursor-pointer",
        getSidebarItemClassName(isActive)
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
            <Badge
              variant="info"
              className="shrink-0 border-transparent px-1.5 py-0"
              title={bridgeTitle}
            >
              <BridgeProviderIcon provider={bridgeProvider} />
            </Badge>
          ) : null}
        </div>
      )}

      {/* Actions menu - visible on hover */}
      {!isEditing && (
        <div
          className="absolute -right-1 top-1/2 z-20 flex -translate-y-1/2 items-center opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100"
          onClick={(e) => e.stopPropagation()}
        >
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7"
                aria-label={t("sidebar.chatActions")}
              >
                <MoreVertical className="h-4 w-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-40">
              <DropdownMenuItem onClick={handleStartEdit}>
                <Pencil className="mr-2 h-4 w-4" />
                {t("sidebar.rename")}
              </DropdownMenuItem>
              <DropdownMenuItem
                onClick={handleDelete}
                className="text-destructive focus:text-destructive"
              >
                <Trash2 className="mr-2 h-4 w-4" />
                {t("common.delete")}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      )}
    </div>
  );
}
