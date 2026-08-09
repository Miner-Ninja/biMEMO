import { Info } from "lucide-react";
import { useTranslation } from "react-i18next";
import { BridgeProviderIcon } from "@/components/icons/BridgeProviderIcon";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ThemeToggle } from "./ThemeToggle";
import { cn } from "@/lib/utils";

interface HeaderProps {
  title?: string;
  bridgeProvider?: string | null;
  onOpenBridgeDetails?: () => void;
  className?: string;
}

/**
 * Application header with title and actions
 */
function bridgeProviderLabel(provider: string): string {
  return provider.charAt(0).toUpperCase() + provider.slice(1);
}

export function Header({
  title,
  bridgeProvider,
  onOpenBridgeDetails,
  className,
}: HeaderProps) {
  const { t } = useTranslation();
  return (
    <header
      className={cn(
        "flex h-11 items-center justify-between gap-1.5 border-b border-border/70 bg-card/80 px-2 sm:h-14 sm:gap-3 sm:px-4",
        className
      )}
    >
      <div className="flex min-w-0 items-center gap-1.5 sm:gap-3">
        <div className="flex min-w-0 items-center gap-1 sm:gap-2">
          <h1 className="truncate text-[15px] font-semibold tracking-tight sm:text-lg">
            {title || t("layout.appTitle")}
          </h1>
          {bridgeProvider ? (
            <Badge
              variant="info"
              className="shrink-0 gap-1 px-1.5 py-0.5 text-[10px] sm:gap-1.5 sm:px-2 sm:text-xs"
              title={t("layout.bridgeConnected", {
                provider: bridgeProviderLabel(bridgeProvider),
              })}
            >
              <BridgeProviderIcon provider={bridgeProvider} className="h-3 w-3" />
              <span className="hidden sm:inline">
                {bridgeProviderLabel(bridgeProvider)}
              </span>
              <Badge variant="success" className="h-2 w-2 rounded-full border-0 p-0" />
            </Badge>
          ) : null}
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-1 sm:gap-2">
        <Button
          type="button"
          variant="ghost"
          size="icon"
          onClick={onOpenBridgeDetails}
          className="h-7.5 w-7.5 sm:h-9 sm:w-9"
          aria-label={t("layout.chatBridgeDetails")}
        >
          <Info className="h-4 w-4 sm:h-4.5 sm:w-4.5" />
        </Button>
        <ThemeToggle />
      </div>
    </header>
  );
}
