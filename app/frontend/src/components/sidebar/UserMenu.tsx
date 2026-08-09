import { useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Popover as PopoverPrimitive } from "@base-ui/react/popover";
import { User, LogOut, Settings, Shield, AudioLines, Languages } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { SUPPORTED_LANGUAGES, isUiLanguage, setUiLanguage } from "@/i18n";
import { getSidebarActionButtonClassName } from "./sidebar-chrome";

interface UserMenuProps {
  userName?: string;
  onLogout?: () => void;
  voiceMode?: boolean;
  onToggleVoiceMode?: () => void;
}

const ITEM_CLASS =
  "relative flex w-full cursor-default select-none items-center gap-2 rounded-sm px-2 py-1.5 text-left text-sm outline-hidden transition-colors hover:bg-accent hover:text-accent-foreground focus-visible:bg-accent focus-visible:text-accent-foreground disabled:pointer-events-none disabled:opacity-50";

const ITEM_DESTRUCTIVE_CLASS =
  "text-destructive hover:bg-destructive/10 hover:text-destructive focus-visible:bg-destructive/10 focus-visible:text-destructive";

const SEPARATOR_CLASS = "my-1 h-px bg-border";

/**
 * User menu with avatar, decorative settings gear, and cursor-anchored popover.
 */
export function UserMenu({
  userName = "User",
  onLogout,
  voiceMode,
  onToggleVoiceMode,
}: UserMenuProps) {
  const location = useLocation();
  const navigate = useNavigate();
  const { t, i18n } = useTranslation();
  const isAdmin = location.pathname.startsWith("/admin");
  const currentLanguage = isUiLanguage(i18n.language) ? i18n.language : "en";
  const nextLanguage =
    SUPPORTED_LANGUAGES[
      (SUPPORTED_LANGUAGES.indexOf(currentLanguage) + 1) % SUPPORTED_LANGUAGES.length
    ];
  const [open, setOpen] = useState(false);
  const [mousePos, setMousePos] = useState<{ x: number; y: number } | null>(null);
  const pendingMousePosRef = useRef<{ x: number; y: number } | null>(null);
  const initials = userName
    .split(" ")
    .map((n) => n[0])
    .join("")
    .toUpperCase()
    .slice(0, 2);

  const anchor = useMemo(() => {
    if (!mousePos) return undefined;
    return {
      getBoundingClientRect: () =>
        DOMRect.fromRect({
          x: mousePos.x,
          y: mousePos.y,
          width: 0,
          height: 0,
        }),
    };
  }, [mousePos]);

  const MOUSE_POS_CLEAR_DELAY_MS = 200;
  const REOPEN_DELAY_MS = 120;

  const handleOpenChange = (nextOpen: boolean) => {
    if (nextOpen) {
      // Opening via trigger click or keyboard. Apply the pointer position
      // captured at the most recent mousedown, if any. Keyboard activations
      // leave the pending ref at null so the popup anchors to the trigger
      // by default.
      setOpen(true);
      if (pendingMousePosRef.current) {
        setMousePos(pendingMousePosRef.current);
        pendingMousePosRef.current = null;
      }
      return;
    }

    // Closing. If a new pointer click is pending (user clicked the trigger
    // again at a different location), animate close and reopen at the new
    // location. Otherwise just close.
    if (pendingMousePosRef.current) {
      const nextPos = pendingMousePosRef.current;
      pendingMousePosRef.current = null;
      setOpen(false);
      setTimeout(() => {
        setMousePos(nextPos);
        setOpen(true);
      }, REOPEN_DELAY_MS);
      return;
    }

    setOpen(false);
    // Defer clearing until the close animation (duration-100) finishes,
    // so the popup animates out at the click point instead of jumping
    // to the trigger anchor mid-animation.
    setTimeout(() => setMousePos(null), MOUSE_POS_CLEAR_DELAY_MS);
  };

  const handleSelect = (action: () => void) => {
    action();
    handleOpenChange(false);
  };

  return (
    <PopoverPrimitive.Root open={open} onOpenChange={handleOpenChange}>
      <PopoverPrimitive.Trigger
        render={
          <Button
            variant="outline"
            className={getSidebarActionButtonClassName()}
            onMouseDown={(e) => {
              pendingMousePosRef.current = { x: e.clientX, y: e.clientY };
            }}
          />
        }
      >
        <Avatar className="size-8">
          <AvatarImage
            src="/avatars/user"
            alt={t("menu.userAvatar")}
            className="object-cover"
          />
          <AvatarFallback className="bg-primary text-primary-foreground text-xs">
            {initials}
          </AvatarFallback>
        </Avatar>
        <span className="flex-1 truncate text-left text-sm">{userName}</span>
        <Settings className="size-4 shrink-0 text-sidebar-foreground/60" aria-hidden />
      </PopoverPrimitive.Trigger>
      <PopoverPrimitive.Portal>
        <PopoverPrimitive.Positioner
          anchor={anchor}
          align="start"
          side="top"
          sideOffset={4}
          className="isolate z-50"
        >
          <PopoverPrimitive.Popup
            className="z-50 flex w-48 origin-(--transform-origin) flex-col rounded-md border bg-popover p-1 text-popover-foreground shadow-md outline-hidden duration-100 data-[side=bottom]:slide-in-from-top-2 data-[side=left]:slide-in-from-right-2 data-[side=right]:slide-in-from-left-2 data-[side=top]:slide-in-from-bottom-2 data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95 data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-95"
            data-slot="user-menu-popup"
          >
            <div className={`${ITEM_CLASS} pointer-events-none opacity-50`}>
              <User className="size-4" />
              {userName}
            </div>
            <div className={SEPARATOR_CLASS} aria-hidden />
            <button
              type="button"
              className={ITEM_CLASS}
              onClick={() => handleSelect(() => navigate(isAdmin ? "/" : "/admin"))}
            >
              <Shield className="size-4" />
              {isAdmin ? t("menu.backToChat") : t("menu.appSettings")}
            </button>
            {onToggleVoiceMode && (
              <button
                type="button"
                className={ITEM_CLASS}
                onClick={(e) => {
                  e.preventDefault();
                  onToggleVoiceMode();
                }}
              >
                <AudioLines className="size-4" />
                <span className="flex-1">{t("menu.voiceMode")}</span>
                <Switch
                  checked={Boolean(voiceMode)}
                  className="pointer-events-none ml-auto scale-75"
                  aria-label={t("menu.voiceMode")}
                />
              </button>
            )}
            <button
              type="button"
              className={ITEM_CLASS}
              onClick={(e) => {
                e.preventDefault();
                setUiLanguage(nextLanguage);
              }}
            >
              <Languages className="size-4" />
              <span className="flex-1">{t("menu.language")}</span>
              <span className="text-xs text-muted-foreground">
                {t(`languages.${currentLanguage}`)}
              </span>
            </button>
            <div className={SEPARATOR_CLASS} aria-hidden />
            <button
              type="button"
              className={`${ITEM_CLASS} ${ITEM_DESTRUCTIVE_CLASS}`}
              onClick={() => handleSelect(() => onLogout?.())}
            >
              <LogOut className="size-4" />
              {t("menu.signOut")}
            </button>
          </PopoverPrimitive.Popup>
        </PopoverPrimitive.Positioner>
      </PopoverPrimitive.Portal>
    </PopoverPrimitive.Root>
  );
}
