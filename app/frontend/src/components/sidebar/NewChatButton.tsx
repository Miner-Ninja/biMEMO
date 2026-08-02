import { Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { getSidebarActionButtonClassName } from "./sidebar-chrome";

interface NewChatButtonProps {
  onClick: () => void;
}

/**
 * Button to create a new chat
 */
export function NewChatButton({ onClick }: NewChatButtonProps) {
  return (
    <Button
      variant="outline"
      className={getSidebarActionButtonClassName()}
      onClick={onClick}
    >
      <Plus className="h-4 w-4" />
      New Chat
    </Button>
  );
}
