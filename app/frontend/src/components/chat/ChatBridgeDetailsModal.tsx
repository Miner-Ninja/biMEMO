import { useEffect, useState } from "react";
import { X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import type {
  ChatBridgeLink,
  ChatBridgeMessageLink,
  ChatBridgeProviderItem,
} from "@/lib/bimemo-api";
import { fetchChatBridgeMessageLinks } from "@/lib/bimemo-api";

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
  return status === "active" ? "Connected" : "Disabled";
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
  direction: string,
): ChatBridgeMessageLink | null {
  const matchingLinks = messageLinks.filter(
    (messageLink) => messageLink.direction === direction,
  );
  return matchingLinks[0] ?? null;
}

function DetailsRow({
  label,
  value,
}: {
  label: string;
  value: string;
}) {
  return (
    <div className="flex flex-col gap-1 border-b border-border/60 py-1.5 text-sm last:border-b-0 sm:flex-row sm:items-start sm:justify-between sm:gap-3">
      <span className="text-muted-foreground sm:max-w-[40%]">{label}</span>
      <span className="break-words text-left sm:max-w-[60%] sm:text-right">
        {value}
      </span>
    </div>
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
  const [showActivity, setShowActivity] = useState(false);
  const [messageLinks, setMessageLinks] = useState<ChatBridgeMessageLink[]>([]);
  const [messageLinksLoading, setMessageLinksLoading] = useState(false);
  const [messageLinksError, setMessageLinksError] = useState("");
  const [personaMode, setPersonaMode] = useState(
    () => bridgeLink?.persona_mode || "neutral",
  );
  const [personaUserId, setPersonaUserId] = useState(
    () => bridgeLink?.persona_user_id || "",
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
              : "Failed to load bridge activity",
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
    : "Not selected";
  const isDiscordBridge = bridgeLink?.provider === "discord";
  const selectedProvider = availableProviders.find(
    ({ provider }) => provider === bridgeLink?.provider,
  );
  const defaultPersonaUserId = selectedProvider?.default_persona_user_id || "";
  const isMappedPersona = personaMode === "mapped_user";
  const settingsChanged =
    !!bridgeLink &&
    (personaMode !== bridgeLink.persona_mode ||
      (isMappedPersona ? personaUserId.trim() : "") !== bridgeLink.persona_user_id);
  const canSaveSettings =
    !!bridgeLink &&
    isDiscordBridge &&
    settingsChanged;
  const visibleMessageLinks = bridgeLink ? messageLinks : [];
  const visibleMessageLinksError = bridgeLink ? messageLinksError : "";
  const latestInboundMessage = latestBridgeMessage(visibleMessageLinks, "inbound");
  const latestOutboundMessage = latestBridgeMessage(visibleMessageLinks, "outbound");
  const latestSyncedMessage = visibleMessageLinks[0] ?? null;

  return (
    <div className="fixed inset-0 z-50 overflow-y-auto bg-black/50 p-2 backdrop-blur-sm sm:flex sm:items-center sm:justify-center sm:p-4">
      <div className="mx-auto flex min-h-[calc(100dvh-1rem)] w-full max-w-lg flex-col rounded-lg border border-border bg-background shadow-lg sm:min-h-0 sm:max-h-[min(90dvh,52rem)]">
        <div className="sticky top-0 z-10 flex items-start justify-between gap-3 border-b border-border bg-background px-3 py-3 sm:px-4">
          <div className="space-y-0.5">
            <h2 className="text-base font-semibold sm:text-lg">Chat bridge properties</h2>
            <p className="text-sm text-muted-foreground">
              {chatTitle || "New Chat"}
            </p>
          </div>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            onClick={onClose}
            className="h-8 w-8 shrink-0"
            aria-label="Close bridge properties"
          >
            <X className="h-4 w-4" />
          </Button>
        </div>

        <div className="flex-1 space-y-2.5 overflow-y-auto px-3 py-3 sm:px-4">
          <div className="rounded-lg border border-border p-2.5">
            <div className="mb-1.5 flex items-center justify-between gap-3">
              <span className="text-sm font-medium">Current state</span>
              <span className="rounded-full border border-primary/20 bg-primary/10 px-2 py-0.5 text-xs text-primary">
                {connectedProvider}
              </span>
            </div>

            {bridgeLink ? (
              <div>
                <DetailsRow
                  label="Status"
                  value={bridgeStatusLabel(bridgeLink.status)}
                />
                <DetailsRow
                  label="Provider"
                  value={bridgeProviderLabel(bridgeLink.provider)}
                />
                <DetailsRow
                  label="Direction"
                  value={bridgeLink.sync_direction}
                />
                <DetailsRow
                  label="Delivery mode"
                  value={bridgeLink.delivery_mode}
                />
                <DetailsRow
                  label="Persona mode"
                  value={bridgeLink.persona_mode}
                />
                <DetailsRow
                  label="Created"
                  value={formatBridgeDate(bridgeLink.created_at)}
                />
                <DetailsRow
                  label="External channel"
                  value={bridgeLink.external_channel_id}
                />
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">
                This chat is not connected to any external messenger yet.
              </p>
            )}
          </div>

          {bridgeLink && isDiscordBridge ? (
            <div className="rounded-lg border border-border p-2.5">
              <div className="mb-1.5">
                <h3 className="text-sm font-medium">How web messages leave for Discord</h3>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  Choose whether web messages should look like a regular web user
                  or like one specific Discord user.
                </p>
              </div>
              <div className="space-y-2">
                <div className="flex items-center justify-between gap-3 rounded-md border border-border/60 px-3 py-2">
                  <div className="space-y-0.5">
                    <p className="text-sm font-medium">Use Discord user ID</p>
                    <p className="text-xs text-muted-foreground">
                      Off: web user. On: one specific Discord user.
                    </p>
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
                </div>
                {isMappedPersona ? (
                  <Input
                    value={personaUserId}
                    onChange={(event) => setPersonaUserId(event.target.value)}
                    placeholder={defaultPersonaUserId || "Enter Discord user ID"}
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
                    {settingsSaving ? "Saving..." : "Save"}
                  </Button>
                </div>
              </div>
            </div>
          ) : null}

          <div className="rounded-lg border border-border p-2.5">
            <h3 className="mb-1 text-sm font-medium">Available providers</h3>
            {availableProviders.length > 0 ? (
              <div className="flex flex-wrap gap-1.5">
                {availableProviders.map(({ provider }) => {
                  const isCurrentProvider = bridgeLink?.provider === provider;
                  return (
                    <span
                      key={provider}
                      className={`rounded-full px-2.5 py-1 text-xs ${
                        isCurrentProvider
                          ? "border border-primary/20 bg-primary/10 text-primary"
                          : "border border-border bg-muted text-muted-foreground"
                      }`}
                    >
                      {bridgeProviderLabel(provider)}
                    </span>
                  );
                })}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">
                No bridge providers are configured right now.
              </p>
            )}
            <p className="mt-1.5 text-xs text-muted-foreground">
              Provider switching still happens from the bridge button in the chat
              input row.
            </p>
          </div>

          <div className="rounded-lg border border-border p-2.5">
            <div className="mb-1.5 flex items-center justify-between gap-3">
              <h3 className="text-sm font-medium">Bridge activity</h3>
              {bridgeLink && !showActivity ? (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={handleLoadActivity}
                  className="h-7 px-2 text-xs"
                >
                  Load activity
                </Button>
              ) : null}
            </div>
            {!bridgeLink ? (
              <p className="text-sm text-muted-foreground">
                Bridge activity will appear here after the first synchronization.
              </p>
            ) : !showActivity ? (
              <p className="text-sm text-muted-foreground">
                Activity is loaded only when you open this section.
              </p>
            ) : messageLinksLoading ? (
              <p className="text-sm text-muted-foreground">
                Loading bridge activity...
              </p>
            ) : visibleMessageLinksError ? (
              <p className="text-sm text-destructive">{visibleMessageLinksError}</p>
            ) : (
              <div>
                <DetailsRow
                  label="Last inbound"
                  value={
                    latestInboundMessage
                      ? formatBridgeDate(latestInboundMessage.created_at)
                      : "No inbound messages yet"
                  }
                />
                <DetailsRow
                  label="Last outbound"
                  value={
                    latestOutboundMessage
                      ? formatBridgeDate(latestOutboundMessage.created_at)
                      : "No outbound messages yet"
                  }
                />
                <DetailsRow
                  label="Last sync"
                  value={
                    latestSyncedMessage
                      ? formatBridgeDate(latestSyncedMessage.created_at)
                      : "No sync activity yet"
                  }
                />
              </div>
            )}
          </div>
        </div>

        <div className="sticky bottom-0 flex justify-end border-t border-border bg-background px-3 py-3 sm:px-4">
          <Button type="button" variant="ghost" onClick={onClose}>
            Close
          </Button>
        </div>
      </div>
    </div>
  );
}
