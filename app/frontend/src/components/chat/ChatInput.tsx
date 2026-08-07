import {
  useState,
  useRef,
  memo,
  forwardRef,
  useImperativeHandle,
  type KeyboardEvent,
  type ChangeEvent,
  type ClipboardEvent,
  type MouseEvent,
} from "react";
import {
  CornerDownLeft,
  Square,
  X,
  FileText,
  AudioLines,
  Waypoints,
  Settings,
  Brain,
  Lightbulb,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Command,
  CommandGroup,
  CommandItem,
  CommandList,
  CommandShortcut,
} from "@/components/ui/command";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { ShimmerText } from "./ShimmerText";
import { LiveTimer } from "./LiveTimer";
import { UploadProgressPanel } from "@/components/ui/UploadProgressPanel";
import type { UploadProgressData } from "@/components/ui/UploadProgressPanel";
import { cn } from "@/lib/utils";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { AttachButton } from "./AttachButton";
import { FileMention } from "./FileMention";
import { VoiceRecorder } from "./VoiceRecorder";
import { useSlashCommands } from "@/hooks/useSlashCommands";
import { useFileMention } from "@/hooks/useFileMention";
import { useCommandHistory } from "@/hooks/useCommandHistory";
import { BridgeProviderIcon } from "@/components/icons/BridgeProviderIcon";

export interface ChatInputHandle {
  setValue: (value: string) => void;
  focus: () => void;
  startVoiceRecording?: () => void;
}

interface ChatInputProps {
  onSend: (message: string, files?: File[]) => void;
  onSendVoice?: (voiceDataUrl: string) => void;
  onAbort?: () => void;
  disabled?: boolean;
  isLoading?: boolean;
  uploadProgress?: UploadProgressData | null;
  placeholder?: string;
  /** Current processing stage; shown as a shimmering label + live timer in
   *  place of the placeholder while a request is processing (empty input only). */
  processingStage?: { label: string; startedAt: number } | null;
  activeFiles?: string[];
  onDeactivateFile?: (filename: string) => void;
  availableFiles?: string[];
  commandHistory?: string[];
  voiceMode?: boolean;
  bridgeProviders?: { provider: string; enabled: boolean }[];
  bridgeProvidersLoading?: boolean;
  bridgeDisabled?: boolean;
  bridgeLoadingProvider?: string | null;
  onBridgeMenuOpenChange?: (open: boolean) => void;
  onToggleBridgeProvider?: (provider: string, enabled: boolean) => void;
  showThinking?: boolean;
  onToggleThinking?: () => void;
  showSuggestions?: boolean;
  onToggleSuggestions?: () => void;
}

function bridgeProviderLabel(provider: string): string {
  return provider.charAt(0).toUpperCase() + provider.slice(1);
}

function normalizeClipboardImageFile(file: File, imageIndex: number): File {
  if (file.name) {
    return file;
  }

  const mimeSubtype = file.type.split("/")[1] || "png";
  return new File([file], `pasted-image-${imageIndex}.${mimeSubtype}`, {
    type: file.type,
  });
}

function autoResizeTextarea(textarea: HTMLTextAreaElement) {
  const computedStyle = window.getComputedStyle(textarea);
  const lineHeight = Number.parseFloat(computedStyle.lineHeight) || 24;
  const paddingTop = Number.parseFloat(computedStyle.paddingTop) || 0;
  const paddingBottom = Number.parseFloat(computedStyle.paddingBottom) || 0;
  const borderTop = Number.parseFloat(computedStyle.borderTopWidth) || 0;
  const borderBottom = Number.parseFloat(computedStyle.borderBottomWidth) || 0;
  const maxHeight =
    lineHeight * 5 + paddingTop + paddingBottom + borderTop + borderBottom;

  textarea.style.height = "auto";
  textarea.style.height = `${Math.min(textarea.scrollHeight, maxHeight)}px`;
  textarea.style.overflowY = textarea.scrollHeight > maxHeight ? "auto" : "hidden";
}

/**
 * Chat input with auto-resize and keyboard shortcuts
 * Wrapped in memo to prevent re-renders during message streaming
 */
export const ChatInput = memo(
  forwardRef<ChatInputHandle, ChatInputProps>(function ChatInput(
    {
      onSend,
      onSendVoice,
      onAbort,
      disabled = false,
      isLoading = false,
      uploadProgress,
      placeholder = "Ask a question about your data...",
      processingStage = null,
      activeFiles,
      onDeactivateFile,
      availableFiles,
      commandHistory: commandHistoryProp = [],
      voiceMode = false,
      bridgeProviders = [],
      bridgeProvidersLoading = false,
      bridgeDisabled = false,
      bridgeLoadingProvider = null,
      onBridgeMenuOpenChange,
      onToggleBridgeProvider,
      showThinking,
      onToggleThinking,
      showSuggestions,
      onToggleSuggestions,
    }: ChatInputProps,
    ref
  ) {
    const [value, setValue] = useState("");
    const [attachedFiles, setAttachedFiles] = useState<File[]>([]);
    const [isRecording, setIsRecording] = useState(false);
    const textareaRef = useRef<HTMLTextAreaElement>(null);

    const {
      showCommands,
      setShowCommands,
      filteredCommands,
      selectedIndex,
      setSelectedIndex,
      handleSelectCommand,
      handleSlashFilter,
      handleSlashKeyDown,
    } = useSlashCommands({ setValue, textareaRef });

    const {
      showFileMention,
      setShowFileMention,
      filteredFiles,
      fileSelectedIndex,
      setFileSelectedIndex,
      handleFileMentionSelect,
      handleMentionFilter,
      handleMentionKeyDown,
    } = useFileMention({ value, setValue, textareaRef, availableFiles });

    const { handleHistoryKeyDown, handleHistoryInputChange, resetHistory } =
      useCommandHistory({ commandHistory: commandHistoryProp, value, setValue });

    useImperativeHandle(ref, () => ({
      setValue: (val: string) => {
        setValue(val);
        setShowCommands(false);
        if (textareaRef.current) {
          autoResizeTextarea(textareaRef.current);
        }
      },
      focus: () => textareaRef.current?.focus(),
      startVoiceRecording: () => {
        if (onSendVoice && !isRecording && !disabled) {
          setIsRecording(true);
        }
      },
    }));

    const handleSubmit = () => {
      const trimmed = value.trim();
      if (!trimmed || disabled) return;

      onSend(trimmed, attachedFiles.length > 0 ? attachedFiles : undefined);
      setValue("");
      setAttachedFiles([]);
      setShowCommands(false);
      resetHistory();

      if (textareaRef.current) {
        textareaRef.current.style.height = "auto";
        textareaRef.current.style.overflowY = "hidden";
      }
    };

    const handleFileSelect = (files: File[]) => {
      setAttachedFiles((prev) => [...prev, ...files]);
    };

    const handlePaste = (event: ClipboardEvent<HTMLTextAreaElement>) => {
      const clipboardItems = Array.from(event.clipboardData?.items ?? []);
      const clipboardImageFiles = clipboardItems
        .filter(
          (clipboardItem) =>
            clipboardItem.kind === "file" && clipboardItem.type.startsWith("image/")
        )
        .map((clipboardItem) => clipboardItem.getAsFile())
        .filter((file): file is File => file !== null)
        .map((file, imageIndex) => normalizeClipboardImageFile(file, imageIndex + 1));

      if (clipboardImageFiles.length === 0) {
        return;
      }

      setAttachedFiles((previousFiles) => [...previousFiles, ...clipboardImageFiles]);
    };

    const handleRemoveFile = (index: number) => {
      setAttachedFiles((prev) => prev.filter((_, i) => i !== index));
    };

    const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if (handleMentionKeyDown(e)) return;
      if (handleSlashKeyDown(e)) return;
      if (handleHistoryKeyDown(e)) return;

      // Enter to send, Shift+Enter for newline
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleSubmit();
      }
    };

    const handleChange = (e: ChangeEvent<HTMLTextAreaElement>) => {
      const newValue = e.target.value;
      setValue(newValue);

      handleHistoryInputChange();
      handleSlashFilter(newValue);

      const cursorPos = e.target.selectionStart ?? newValue.length;
      handleMentionFilter(newValue, cursorPos);

      autoResizeTextarea(e.target);
    };

    const handleBlur = () => {
      setTimeout(() => {
        setShowCommands(false);
        setShowFileMention(false);
      }, 150);
    };

    const canSend = value.trim().length > 0 && !disabled;
    // Show the shimmering stage label in the placeholder spot only while a
    // request is processing AND the input is empty (so typing hides it).
    const showStageOverlay = !!processingStage && value.length === 0;

    return (
      <div
        className={cn(
          "bg-background px-1.5 py-1.5 pb-[calc(env(safe-area-inset-bottom)+0.35rem)] sm:p-4",
          isLoading && "[&_*]:cursor-wait",
          voiceMode && "border-t-primary/50"
        )}
      >
        {/* Container with max-width and horizontal padding for large screens */}
        <div className="mx-auto w-full max-w-[1920px] px-1.5 sm:px-0 2xl:px-[10%]">
          {/* Active files context chips */}
          {activeFiles && activeFiles.length > 0 && (
            <div className="mx-auto mb-2 flex max-w-3xl flex-wrap justify-start gap-1.5 sm:justify-center">
              {activeFiles.map((filename) => (
                <Card
                  key={filename}
                  className="max-w-full border-primary/20 bg-primary/10 shadow-none"
                >
                  <CardContent className="flex items-center gap-1.5 px-2.5 py-1">
                    <Badge variant="info" className="gap-1.5 border-transparent">
                      <FileText className="h-3 w-3" />
                      <span className="max-w-[140px] truncate sm:max-w-[180px]">
                        {filename}
                      </span>
                    </Badge>
                    {onDeactivateFile && (
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        onClick={() => onDeactivateFile(filename)}
                        className="h-5 w-5 rounded-full p-0"
                        aria-label={`Deactivate ${filename}`}
                      >
                        <X className="h-3 w-3" />
                      </Button>
                    )}
                  </CardContent>
                </Card>
              ))}
            </div>
          )}

          {/* Attached files preview */}
          {attachedFiles.length > 0 && (
            <div className="mx-auto mb-2 flex max-w-3xl flex-wrap justify-start gap-1.5 sm:justify-center">
              {attachedFiles.map((file, index) => (
                <Card
                  key={`${file.name}-${index}`}
                  className="max-w-full bg-muted/70 shadow-none"
                >
                  <CardContent className="flex items-center gap-2 px-3 py-1.5">
                    <Badge
                      variant="secondary"
                      className="gap-2 border-transparent text-sm"
                    >
                      <FileText className="h-4 w-4 text-muted-foreground" />
                      <span className="max-w-[130px] truncate sm:max-w-[200px]">
                        {file.name}
                      </span>
                      <span className="hidden text-xs text-muted-foreground sm:inline">
                        ({(file.size / 1024).toFixed(1)} KB)
                      </span>
                    </Badge>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      onClick={() => handleRemoveFile(index)}
                      className="h-5 w-5 p-0"
                      aria-label="Remove file"
                    >
                      <X className="h-3 w-3" />
                    </Button>
                  </CardContent>
                </Card>
              ))}
            </div>
          )}

          {/* Upload progress indicator */}
          {uploadProgress && (
            <div className="mx-auto mb-2 max-w-3xl">
              <UploadProgressPanel progress={uploadProgress} />
            </div>
          )}

          {voiceMode && (
            <Badge
              variant="info"
              className="mx-auto mb-2 flex max-w-3xl items-center justify-center gap-1.5 px-2 py-1 text-center text-xs"
            >
              <AudioLines className="h-3.5 w-3.5 animate-pulse" />
              <span>Voice mode active</span>
            </Badge>
          )}

          <div className="relative mx-auto max-w-3xl">
            {/* @mention file autocomplete popup */}
            {showFileMention && (
              <div className="absolute bottom-full left-0 right-0 z-50 mb-2 sm:ml-11">
                <FileMention
                  files={filteredFiles}
                  selectedIndex={fileSelectedIndex}
                  onSelect={handleFileMentionSelect}
                  onHover={setFileSelectedIndex}
                />
              </div>
            )}

            {/* Slash command autocomplete popup */}
            {showCommands && filteredCommands.length > 0 && (
              <div className="absolute bottom-full left-0 right-0 z-50 mb-2 sm:ml-11">
                <Card className="shadow-md">
                  <CardContent className="p-1">
                    <Command shouldFilter={false} className="bg-transparent">
                      <CommandList>
                        <CommandGroup heading="Commands">
                          {filteredCommands.map((cmd, index) => (
                            <CommandItem
                              key={cmd.name}
                              value={cmd.name}
                              onMouseDown={(e: MouseEvent<HTMLDivElement>) => {
                                e.preventDefault();
                                handleSelectCommand(cmd);
                              }}
                              onMouseEnter={() => setSelectedIndex(index)}
                              onSelect={() => handleSelectCommand(cmd)}
                              className={cn(
                                "gap-3 px-3 py-2",
                                index === selectedIndex &&
                                  "bg-accent text-accent-foreground"
                              )}
                            >
                              <div className="flex min-w-0 flex-1 flex-col gap-1">
                                <span className="font-medium text-primary">
                                  {cmd.name}
                                </span>
                                <span className="truncate text-xs text-muted-foreground">
                                  {cmd.description}
                                </span>
                              </div>
                              <CommandShortcut>
                                {cmd.hasArgs ? "args" : "ready"}
                              </CommandShortcut>
                            </CommandItem>
                          ))}
                        </CommandGroup>
                      </CommandList>
                    </Command>
                  </CardContent>
                </Card>
              </div>
            )}

            {/* Input row */}
            <div className="rounded-lg border border-border bg-card shadow-xs sm:rounded-xl">
              {isRecording && onSendVoice ? (
                <VoiceRecorder
                  onRecorded={onSendVoice}
                  onRecordingChange={setIsRecording}
                  disabled={disabled}
                  autoStart
                  embedded
                  className="w-full px-3 pt-2 sm:px-4 sm:pt-2.5"
                />
              ) : (
                <div className="relative">
                  <Textarea
                    ref={textareaRef}
                    value={value}
                    onChange={handleChange}
                    onKeyDown={handleKeyDown}
                    onPaste={handlePaste}
                    onBlur={handleBlur}
                    placeholder={showStageOverlay ? "" : placeholder}
                    disabled={disabled}
                    rows={1}
                    className={cn(
                      "min-h-[38px] max-h-[160px] resize-none overflow-y-hidden border-0 bg-transparent px-3 py-1.5 text-[15px] shadow-none sm:min-h-[44px] sm:px-4 sm:py-2.5 sm:text-base",
                      "focus-visible:ring-0 focus-visible:border-0"
                    )}
                  />
                  {showStageOverlay && processingStage ? (
                    <div
                      aria-hidden
                      className="pointer-events-none absolute inset-x-0 top-0 flex items-center gap-2 px-3 py-1.5 text-[15px] text-muted-foreground sm:px-4 sm:py-2.5 sm:text-base"
                    >
                      <span className="min-w-0 truncate">
                        <ShimmerText text={processingStage.label} />
                      </span>
                      <LiveTimer startedAt={processingStage.startedAt} />
                    </div>
                  ) : null}
                </div>
              )}
              <div className="flex items-center justify-between gap-2 py-1.5 pl-1.5 pr-1 sm:py-1.5 sm:pl-2 sm:pr-1">
                <div className="flex items-center gap-1">
                  <AttachButton
                    onFileSelect={handleFileSelect}
                    disabled={disabled || isRecording}
                    className="h-7.5 w-7.5 sm:h-9 sm:w-9"
                  />
                  {onSendVoice && !canSend && !isRecording ? (
                    <VoiceRecorder
                      onRecorded={onSendVoice}
                      onRecordingChange={setIsRecording}
                      disabled={disabled}
                      className="h-7.5 w-7.5 sm:h-9 sm:w-9"
                    />
                  ) : null}
                  {onToggleBridgeProvider ? (
                    <DropdownMenu onOpenChange={onBridgeMenuOpenChange}>
                      <DropdownMenuTrigger asChild>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          disabled={disabled || bridgeDisabled}
                          className="h-7.5 w-7.5 sm:h-9 sm:w-9"
                          aria-label="Bridge providers"
                        >
                          <Waypoints className="h-4 w-4 sm:h-4.5 sm:w-4.5" />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="start" className="w-64">
                        <DropdownMenuLabel>Bridge providers</DropdownMenuLabel>
                        <DropdownMenuSeparator />
                        {bridgeProvidersLoading ? (
                          <div className="p-2 text-sm text-muted-foreground">
                            Loading providers...
                          </div>
                        ) : bridgeProviders.length > 0 ? (
                          <DropdownMenuGroup>
                            {bridgeProviders.map(({ provider, enabled }) => {
                              const isLoadingProvider =
                                bridgeLoadingProvider === provider;
                              return (
                                <DropdownMenuItem
                                  key={provider}
                                  disabled={
                                    disabled || bridgeDisabled || isLoadingProvider
                                  }
                                  onClick={() =>
                                    onToggleBridgeProvider(provider, !enabled)
                                  }
                                >
                                  <span className="flex items-center gap-2">
                                    <BridgeProviderIcon
                                      provider={provider}
                                      className="h-4 w-4"
                                    />
                                    {bridgeProviderLabel(provider)}
                                  </span>
                                  <Switch
                                    checked={enabled}
                                    disabled={
                                      disabled || bridgeDisabled || isLoadingProvider
                                    }
                                  />
                                </DropdownMenuItem>
                              );
                            })}
                          </DropdownMenuGroup>
                        ) : (
                          <div className="p-2 text-sm text-muted-foreground">
                            No bridge providers are configured right now.
                          </div>
                        )}
                      </DropdownMenuContent>
                    </DropdownMenu>
                  ) : null}
                  {onToggleThinking || onToggleSuggestions ? (
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="h-7.5 w-7.5 sm:h-9 sm:w-9"
                          aria-label="Chat Settings"
                        >
                          <Settings className="h-4 w-4 sm:h-4.5 sm:w-4.5" />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="start" className="w-56">
                        <DropdownMenuLabel>Chat Settings</DropdownMenuLabel>
                        <DropdownMenuSeparator />
                        <DropdownMenuGroup>
                          {onToggleThinking && (
                            <DropdownMenuItem onClick={onToggleThinking}>
                              <span className="flex items-center gap-2">
                                <Brain className="h-4 w-4" />
                                Show Thinking
                              </span>
                              <Switch checked={Boolean(showThinking)} />
                            </DropdownMenuItem>
                          )}
                          {onToggleSuggestions && (
                            <DropdownMenuItem onClick={onToggleSuggestions}>
                              <span className="flex items-center gap-2">
                                <Lightbulb className="h-4 w-4" />
                                Suggestions
                              </span>
                              <Switch checked={Boolean(showSuggestions)} />
                            </DropdownMenuItem>
                          )}
                        </DropdownMenuGroup>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  ) : null}
                </div>
                {isLoading ? (
                  <Button
                    onClick={onAbort}
                    variant="destructive"
                    size="icon"
                    className="h-8 w-8 shrink-0 rounded-md sm:h-9 sm:w-9 sm:rounded-lg"
                    aria-label="Stop generation"
                  >
                    <Square className="h-4 w-4 sm:h-4.5 sm:w-4.5" />
                  </Button>
                ) : (
                  <Button
                    onClick={handleSubmit}
                    disabled={!canSend || isRecording}
                    size="icon"
                    className="h-8 w-8 shrink-0 rounded-md sm:h-9 sm:w-9 sm:rounded-lg"
                    aria-label="Send message"
                  >
                    <CornerDownLeft className="h-4 w-4 sm:h-4.5 sm:w-4.5" />
                  </Button>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
    );
  })
);
