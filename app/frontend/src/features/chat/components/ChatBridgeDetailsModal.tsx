import { useEffect, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import i18n from "@/i18n";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { Switch } from "@/components/ui/switch";
import type {
  ChatBridgeLink,
  ChatBridgeMessageLink,
  ChatBridgeProviderItem,
} from "@/services/bimemo-api";
import { fetchChatBridgeMessageLinks } from "@/services/bimemo-api";

interface ChatBridgeDetailsModalProps {
  chatTitle: string;
  bridgeLink: ChatBridgeLink | null;
  availableProviders: ChatBridgeProviderItem[];
  settingsSaving: boolean;
  onSaveSettings: (settings: {
    deliveryMode: string;
    personaMode: string;
    personaUserId: string;
  }) => void;
  onClose: () => void;
}

function bridgeProviderLabel(provider: string): string {
  return provider.charAt(0).toUpperCase() + provider.slice(1);
}

function bridgeStatusLabel(status: string): string {
  return status === "active"
    ? i18n.t("chat.statusConnected")
    : i18n.t("chat.statusDisabled");
}

function formatBridgeDate(createdAt: string): string {
  const parsedDate = new Date(createdAt);
  if (Number.isNaN(parsedDate.getTime())) {
    return createdAt;
  }
  return parsedDate.toLocaleString();
}

function latestBridgeMessage(
  messageLinks: ChatBridgeMessageLink[],
  direction: string
): ChatBridgeMessageLink | null {
  const matchingLinks = messageLinks.filter(
    (messageLink) => messageLink.direction === direction
  );
  return matchingLinks[0] ?? null;
}

function DetailsRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-1 sm:flex-row sm:items-start sm:justify-between sm:gap-3">
      <span className="text-sm text-muted-foreground sm:max-w-[40%]">{label}</span>
      <span className="break-words text-sm sm:max-w-[60%] sm:text-right">{value}</span>
    </div>
  );
}

function SectionCard({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: ReactNode;
}) {
  return (
    <Card>
      <CardHeader className="p-4 pb-3">
        <CardTitle className="text-sm">{title}</CardTitle>
        {description ? (
          <DialogDescription className="text-xs">{description}</DialogDescription>
        ) : null}
      </CardHeader>
      <Separator />
      <CardContent className="flex flex-col gap-3 p-4 pt-4">{children}</CardContent>
    </Card>
  );
}

export function ChatBridgeDetailsModal({
  chatTitle,
  bridgeLink,
  availableProviders,
  settingsSaving,
  onSaveSettings,
  onClose,
}: ChatBridgeDetailsModalProps) {
  const { t } = useTranslation();
  const [showActivity, setShowActivity] = useState(false);
  const [messageLinks, setMessageLinks] = useState<ChatBridgeMessageLink[]>([]);
  const [messageLinksLoading, setMessageLinksLoading] = useState(false);
  const [messageLinksError, setMessageLinksError] = useState("");
  const [personaMode, setPersonaMode] = useState(
    () => bridgeLink?.persona_mode || "neutral"
  );
  const [personaUserId, setPersonaUserId] = useState(
    () => bridgeLink?.persona_user_id || ""
  );

  useEffect(() => {
    if (!bridgeLink || !showActivity) {
      return;
    }

    let cancelled = false;

    fetchChatBridgeMessageLinks(bridgeLink.id)
      .then((loadedMessageLinks) => {
        if (!cancelled) {
          setMessageLinks(loadedMessageLinks);
          setMessageLinksError("");
        }
      })
      .catch((error) => {
        if (!cancelled) {
          setMessageLinks([]);
          setMessageLinksError(
            error instanceof Error
              ? error.message
              : i18n.t("chat.failedToLoadBridgeActivity")
          );
        }
      })
      .finally(() => {
        if (!cancelled) {
          setMessageLinksLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [bridgeLink, showActivity]);

  function handleLoadActivity() {
    setMessageLinks([]);
    setMessageLinksError("");
    setMessageLinksLoading(true);
    setShowActivity(true);
  }

  function handleSaveSettings() {
    if (!bridgeLink) {
      return;
    }
    onSaveSettings({
      deliveryMode:
        bridgeLink.provider === "discord"
          ? "persona_user_and_bot_assistant"
          : bridgeLink.delivery_mode,
      personaMode,
      personaUserId: personaMode === "mapped_user" ? personaUserId.trim() : "",
    });
  }

  const connectedProvider = bridgeLink
    ? bridgeProviderLabel(bridgeLink.provider)
    : t("chat.notSelected");
  const isDiscordBridge = bridgeLink?.provider === "discord";
  const selectedProvider = availableProviders.find(
    ({ provider }) => provider === bridgeLink?.provider
  );
  const defaultPersonaUserId = selectedProvider?.default_persona_user_id || "";
  const isMappedPersona = personaMode === "mapped_user";
  const settingsChanged =
    !!bridgeLink &&
    (personaMode !== bridgeLink.persona_mode ||
      (isMappedPersona ? personaUserId.trim() : "") !== bridgeLink.persona_user_id);
  const canSaveSettings = !!bridgeLink && isDiscordBridge && settingsChanged;
  const visibleMessageLinks = bridgeLink ? messageLinks : [];
  const visibleMessageLinksError = bridgeLink ? messageLinksError : "";
  const latestInboundMessage = latestBridgeMessage(visibleMessageLinks, "inbound");
  const latestOutboundMessage = latestBridgeMessage(visibleMessageLinks, "outbound");
  const latestSyncedMessage = visibleMessageLinks[0] ?? null;

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="w-[calc(100vw-1rem)] max-w-3xl p-0 sm:max-h-[90dvh]">
        <DialogHeader className="p-6 pb-0">
          <DialogTitle>{t("chat.bridgeProperties")}</DialogTitle>
          <DialogDescription>{chatTitle || t("chat.newChat")}</DialogDescription>
        </DialogHeader>

        <ScrollArea className="max-h-[calc(90dvh-9rem)] px-6">
          <div className="flex flex-col gap-4 py-6">
            <SectionCard title={t("chat.currentState")}>
              <div className="flex items-center justify-between gap-3">
                <span className="text-sm text-muted-foreground">
                  {t("chat.connectedProvider")}
                </span>
                <Badge variant="info">{connectedProvider}</Badge>
              </div>
              <Separator />
              {bridgeLink ? (
                <div className="flex flex-col gap-3">
                  <DetailsRow
                    label={t("chat.status")}
                    value={bridgeStatusLabel(bridgeLink.status)}
                  />
                  <Separator />
                  <DetailsRow
                    label={t("chat.provider")}
                    value={bridgeProviderLabel(bridgeLink.provider)}
                  />
                  <Separator />
                  <DetailsRow
                    label={t("chat.direction")}
                    value={bridgeLink.sync_direction}
                  />
                  <Separator />
                  <DetailsRow
                    label={t("chat.deliveryMode")}
                    value={bridgeLink.delivery_mode}
                  />
                  <Separator />
                  <DetailsRow
                    label={t("chat.personaMode")}
                    value={bridgeLink.persona_mode}
                  />
                  <Separator />
                  <DetailsRow
                    label={t("chat.created")}
                    value={formatBridgeDate(bridgeLink.created_at)}
                  />
                  <Separator />
                  <DetailsRow
                    label={t("chat.externalChannel")}
                    value={bridgeLink.external_channel_id}
                  />
                </div>
              ) : (
                <div className="text-sm text-muted-foreground">
                  {t("chat.notConnectedToMessenger")}
                </div>
              )}
            </SectionCard>

            {bridgeLink && isDiscordBridge ? (
              <SectionCard
                title={t("chat.webMessagesToDiscord")}
                description={t("chat.webMessagesToDiscordDescription")}
              >
                <Card className="border-border/70 bg-muted/30 shadow-none">
                  <CardContent className="flex items-center justify-between gap-3 p-4">
                    <div className="flex flex-col gap-0.5">
                      <div className="text-sm font-medium">
                        {t("chat.useDiscordUserId")}
                      </div>
                      <div className="text-xs text-muted-foreground">
                        {t("chat.discordPersonaHint")}
                      </div>
                    </div>
                    <Switch
                      checked={isMappedPersona}
                      onCheckedChange={(checked) => {
                        setPersonaMode(checked ? "mapped_user" : "neutral");
                        if (checked && !personaUserId.trim() && defaultPersonaUserId) {
                          setPersonaUserId(defaultPersonaUserId);
                        }
                      }}
                    />
                  </CardContent>
                </Card>
                {isMappedPersona ? (
                  <Input
                    value={personaUserId}
                    onChange={(event) => setPersonaUserId(event.target.value)}
                    placeholder={defaultPersonaUserId || t("chat.enterDiscordUserId")}
                    className="break-all"
                  />
                ) : null}
                <div className="flex justify-end">
                  <Button
                    type="button"
                    size="sm"
                    onClick={handleSaveSettings}
                    disabled={!canSaveSettings || settingsSaving}
                  >
                    {settingsSaving ? t("chat.saving") : t("common.save")}
                  </Button>
                </div>
              </SectionCard>
            ) : null}

            <SectionCard
              title={t("chat.availableProviders")}
              description={t("chat.providerSwitchingHint")}
            >
              {availableProviders.length > 0 ? (
                <div className="flex flex-wrap gap-2">
                  {availableProviders.map(({ provider }) => {
                    const isCurrentProvider = bridgeLink?.provider === provider;
                    return (
                      <Badge
                        key={provider}
                        variant={isCurrentProvider ? "info" : "outline"}
                      >
                        {bridgeProviderLabel(provider)}
                      </Badge>
                    );
                  })}
                </div>
              ) : (
                <div className="text-sm text-muted-foreground">
                  {t("chat.noBridgeProviders")}
                </div>
              )}
            </SectionCard>

            <SectionCard title={t("chat.bridgeActivity")}>
              <div className="flex items-center justify-between gap-3">
                <div className="text-sm text-muted-foreground">
                  {!bridgeLink
                    ? t("chat.bridgeActivityEmpty")
                    : !showActivity
                      ? t("chat.activityLoadHint")
                      : null}
                </div>
                {bridgeLink && !showActivity ? (
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={handleLoadActivity}
                  >
                    {t("chat.loadActivity")}
                  </Button>
                ) : null}
              </div>
              {showActivity ? (
                messageLinksLoading ? (
                  <div className="text-sm text-muted-foreground">
                    {t("chat.loadingBridgeActivity")}
                  </div>
                ) : visibleMessageLinksError ? (
                  <div className="text-sm text-destructive">
                    {visibleMessageLinksError}
                  </div>
                ) : (
                  <div className="flex flex-col gap-3">
                    <DetailsRow
                      label={t("chat.lastInbound")}
                      value={
                        latestInboundMessage
                          ? formatBridgeDate(latestInboundMessage.created_at)
                          : t("chat.noInboundMessages")
                      }
                    />
                    <Separator />
                    <DetailsRow
                      label={t("chat.lastOutbound")}
                      value={
                        latestOutboundMessage
                          ? formatBridgeDate(latestOutboundMessage.created_at)
                          : t("chat.noOutboundMessages")
                      }
                    />
                    <Separator />
                    <DetailsRow
                      label={t("chat.lastSync")}
                      value={
                        latestSyncedMessage
                          ? formatBridgeDate(latestSyncedMessage.created_at)
                          : t("chat.noSyncActivity")
                      }
                    />
                  </div>
                )
              ) : null}
            </SectionCard>
          </div>
        </ScrollArea>

        <DialogFooter className="border-t border-border px-6 py-4">
          <Button type="button" variant="ghost" onClick={onClose}>
            {t("common.close")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
