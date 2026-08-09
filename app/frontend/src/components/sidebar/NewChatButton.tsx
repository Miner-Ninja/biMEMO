import { Plus } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { getSidebarActionButtonClassName } from "./sidebar-chrome";

interface NewChatButtonProps {
  onClick: () => void;
}

/**
 * Button to create a new chat
 */
export function NewChatButton({ onClick }: NewChatButtonProps) {
  const { t } = useTranslation();
  return (
    <Button
      variant="outline"
      className={getSidebarActionButtonClassName()}
      onClick={onClick}
    >
      <Plus className="h-4 w-4" />
      {t("sidebar.newChat")}
    </Button>
  );
}
