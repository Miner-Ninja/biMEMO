import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import type { TranslationKey } from "@/i18n";
import type {
  FileContextData,
  FileContextIndexEntry,
} from "@/components/admin/memory-debug/types";
import {
  EmptyStateCard,
  SectionIntro,
  TypeBadge,
} from "@/components/admin/memory-debug/shared";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { apiRequest } from "@/lib/bimemo-api";

const SUMMARY_BLOCK_MARKER = "Stored file summary:";
const CHUNK_BLOCK_MARKER = "Selected file chunks for this request:";
const PREVIEW_BLOCK_MARKER = "File preview:";
const FULL_CONTENT_BLOCK_MARKER = "Full file content:";
const FILE_METADATA_BLOCK_MARKER = "Attached file:";

function formatIsoTimestamp(value: string): string {
  return new Date(value).toLocaleString();
}

function formatChunkIndexes(indexes: number[]): string {
  return indexes.map((index) => String(index + 1)).join(", ");
}

function getPromptBlockDetails(block: string): {
  labelKey: TranslationKey;
  descriptionKey: TranslationKey;
} {
  if (block.includes(CHUNK_BLOCK_MARKER)) {
    return {
      labelKey: "memoryDebug.blockSelectedChunks",
      descriptionKey: "memoryDebug.blockSelectedChunksDescription",
    };
  }
  if (block.includes(SUMMARY_BLOCK_MARKER)) {
    return {
      labelKey: "memoryDebug.blockStoredSummary",
      descriptionKey: "memoryDebug.blockStoredSummaryDescription",
    };
  }
  if (block.includes(PREVIEW_BLOCK_MARKER)) {
    return {
      labelKey: "memoryDebug.blockFilePreview",
      descriptionKey: "memoryDebug.blockFilePreviewDescription",
    };
  }
  if (block.includes(FULL_CONTENT_BLOCK_MARKER)) {
    return {
      labelKey: "memoryDebug.blockInlineContent",
      descriptionKey: "memoryDebug.blockInlineContentDescription",
    };
  }
  if (block.includes(FILE_METADATA_BLOCK_MARKER)) {
    return {
      labelKey: "memoryDebug.blockFileMetadata",
      descriptionKey: "memoryDebug.blockFileMetadataDescription",
    };
  }
  return {
    labelKey: "memoryDebug.blockPromptContext",
    descriptionKey: "memoryDebug.blockPromptContextDescription",
  };
}

export function FileContextPanel({ refreshToken }: { refreshToken: number }) {
  const { t } = useTranslation();
  const [files, setFiles] = useState<FileContextIndexEntry[]>([]);
  const [selectedFileId, setSelectedFileId] = useState("");
  const [fileContext, setFileContext] = useState<FileContextData | null>(null);
  const [filesError, setFilesError] = useState("");
  const [contextError, setContextError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setFilesError("");

    apiRequest<{ files: FileContextIndexEntry[] }>("/admin/memory/files")
      .then((data) => {
        if (cancelled) return;
        const nextFiles = data.files || [];
        setFiles(nextFiles);
        setSelectedFileId((current) => {
          const firstFile = nextFiles[0];
          if (!firstFile) return "";
          return nextFiles.some((entry) => entry.id === current)
            ? current
            : firstFile.id;
        });
      })
      .catch((requestError) => {
        if (cancelled) return;
        setFiles([]);
        setSelectedFileId("");
        setFilesError(String(requestError));
      });

    return () => {
      cancelled = true;
    };
  }, [refreshToken]);

  useEffect(() => {
    if (!selectedFileId) {
      setFileContext(null);
      setContextError("");
      return;
    }

    let cancelled = false;
    setContextError("");

    apiRequest<{ file_context: FileContextData | null }>(
      `/admin/memory/files/${encodeURIComponent(selectedFileId)}/context`
    )
      .then((data) => {
        if (cancelled) return;
        setFileContext(data.file_context);
      })
      .catch((requestError) => {
        if (cancelled) return;
        setFileContext(null);
        setContextError(String(requestError));
      });

    return () => {
      cancelled = true;
    };
  }, [selectedFileId, refreshToken]);

  if (filesError) {
    return (
      <div className="p-4 text-sm text-red-400">
        {t("memoryDebug.filesIndexLoadError", { error: filesError })}
      </div>
    );
  }

  const firstFile = files[0];
  if (!firstFile) {
    return (
      <EmptyStateCard
        title={t("memoryDebug.noFileContextTitle")}
        description={t("memoryDebug.noFileContextDescription")}
      />
    );
  }

  const selectedFile = files.find((entry) => entry.id === selectedFileId) || firstFile;
  const selectedChunkIds = new Set(fileContext?.last_usage?.selected_chunk_ids || []);
  const selectedChunkIndexes = new Set(
    fileContext?.last_usage?.selected_chunk_indexes || []
  );

  return (
    <div className="space-y-6">
      <SectionIntro
        title={t("memoryDebug.viewFiles")}
        description={t("memoryDebug.fileContextIntro")}
      />

      <Card>
        <CardHeader>
          <CardTitle>{t("memoryDebug.attachedFiles")}</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-2">
          {files.map((file) => (
            <Button
              key={file.id}
              size="sm"
              variant={file.id === selectedFileId ? "default" : "outline"}
              onClick={() => setSelectedFileId(file.id)}
            >
              {file.original_name}
            </Button>
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <TypeBadge type="file" />
            <CardTitle className="text-base">{selectedFile.original_name}</CardTitle>
            {fileContext ? (
              <Badge variant="outline">{fileContext.processing_mode}</Badge>
            ) : null}
          </div>
          <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
            <span>{t("memoryDebug.mimeLabel", { value: selectedFile.mime_type })}</span>
            <span>{t("memoryDebug.sizeBytes", { size: selectedFile.size })}</span>
            <span>
              {t("memoryDebug.uploadedAt", {
                time: formatIsoTimestamp(selectedFile.created_at),
              })}
            </span>
            {fileContext ? (
              <span>
                {t("memoryDebug.kindLabel", { value: fileContext.file_kind })}
              </span>
            ) : null}
            {fileContext ? (
              <span>
                {t("memoryDebug.estimatedTokens", {
                  value: fileContext.estimated_tokens,
                })}
              </span>
            ) : null}
          </div>
        </CardHeader>
        <CardContent className="space-y-6">
          {contextError ? (
            <div className="text-sm text-red-400">
              {t("memoryDebug.fileContextLoadError", { error: contextError })}
            </div>
          ) : null}

          {!contextError && !fileContext ? (
            <div className="font-prose text-sm text-muted-foreground">
              {t("memoryDebug.noStoredTextContext")}
            </div>
          ) : null}

          {fileContext ? (
            <>
              <div className="space-y-3">
                <h3 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
                  {t("memoryDebug.storedModeReason")}
                </h3>
                <div className="rounded-lg border border-border bg-muted/10 p-4 text-sm leading-7">
                  {fileContext.processing_reason || t("memoryDebug.noModeExplanation")}
                </div>
              </div>

              <div className="space-y-3">
                <h3 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
                  {t("memoryDebug.summaryOverview")}
                </h3>
                <div className="rounded-lg border border-border bg-muted/10 p-4 text-sm leading-7">
                  {fileContext.summary_overview || t("memoryDebug.noSummaryOverview")}
                </div>
              </div>

              <Card className="border-border/70 bg-muted/10">
                <CardHeader className="pb-3">
                  <CardTitle className="text-sm">
                    {t("memoryDebug.lastPromptUsage")}
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                  {fileContext.last_usage ? (
                    <>
                      <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
                        <span>
                          {t("memoryDebug.modeLabel", {
                            value: fileContext.last_usage.processing_mode,
                          })}
                        </span>
                        <span>
                          {t("memoryDebug.usedAt", {
                            time: formatIsoTimestamp(fileContext.last_usage.used_at),
                          })}
                        </span>
                        <span>
                          {t("memoryDebug.promptChunks", {
                            value: fileContext.last_usage.selected_chunk_indexes.length
                              ? formatChunkIndexes(
                                  fileContext.last_usage.selected_chunk_indexes
                                )
                              : t("memoryDebug.noChunksSelected"),
                          })}
                        </span>
                      </div>
                      <div className="space-y-2">
                        <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                          {t("memoryDebug.whyModeUsed")}
                        </h4>
                        <div className="rounded-md border border-border/70 bg-background/60 p-3 text-sm leading-7">
                          {fileContext.last_usage.mode_reason ||
                            t("memoryDebug.noLastModeExplanation")}
                        </div>
                      </div>
                      <div className="space-y-2">
                        <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                          {t("memoryDebug.lastQuery")}
                        </h4>
                        <div className="rounded-md border border-border/70 bg-background/60 p-3 text-sm leading-7">
                          {fileContext.last_usage.user_query ||
                            t("memoryDebug.noStoredQuery")}
                        </div>
                      </div>
                    </>
                  ) : (
                    <div className="font-prose text-sm text-muted-foreground">
                      {t("memoryDebug.fileNotUsedSinceTracking")}
                    </div>
                  )}
                </CardContent>
              </Card>

              <div className="space-y-3">
                <h3 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
                  {t("memoryDebug.rawPromptBlocksPreview")}
                </h3>
                {fileContext.prompt_blocks.length ? (
                  <div className="space-y-3">
                    {fileContext.prompt_blocks.map((block, index) => (
                      <Card
                        key={`${selectedFile.id}-prompt-${index}`}
                        className="border-border/70 bg-muted/10"
                      >
                        <CardHeader className="pb-3">
                          <CardTitle className="text-sm">
                            {t("memoryDebug.promptBlockNumber", {
                              number: index + 1,
                            })}
                          </CardTitle>
                        </CardHeader>
                        <CardContent>
                          <pre className="overflow-x-auto whitespace-pre-wrap break-words rounded-md border border-border/70 bg-background/60 p-3 text-xs leading-6 text-foreground/90">
                            {block}
                          </pre>
                        </CardContent>
                      </Card>
                    ))}
                  </div>
                ) : (
                  <div className="font-prose text-sm text-muted-foreground">
                    {t("memoryDebug.noPromptPreview")}
                  </div>
                )}
              </div>

              <div className="space-y-3">
                <h3 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
                  {t("memoryDebug.forensicView")}
                </h3>
                <div className="grid gap-4 xl:grid-cols-4">
                  <Card className="border-border/70 bg-muted/10">
                    <CardHeader className="pb-3">
                      <CardTitle className="text-sm">
                        {t("memoryDebug.storedMode")}
                      </CardTitle>
                    </CardHeader>
                    <CardContent className="text-sm text-foreground/90">
                      {fileContext.processing_mode}
                    </CardContent>
                  </Card>
                  <Card className="border-border/70 bg-muted/10">
                    <CardHeader className="pb-3">
                      <CardTitle className="text-sm">
                        {t("memoryDebug.lastPromptMode")}
                      </CardTitle>
                    </CardHeader>
                    <CardContent className="text-sm text-foreground/90">
                      {fileContext.last_usage?.processing_mode ||
                        t("memoryDebug.fileNotUsedInPrompt")}
                    </CardContent>
                  </Card>
                  <Card className="border-border/70 bg-muted/10">
                    <CardHeader className="pb-3">
                      <CardTitle className="text-sm">
                        {t("memoryDebug.chunkSelection")}
                      </CardTitle>
                    </CardHeader>
                    <CardContent className="font-prose text-sm text-foreground/90">
                      {t("memoryDebug.chunksUsedOfStored", {
                        used: selectedChunkIds.size,
                        total: fileContext.chunks.length,
                      })}
                    </CardContent>
                  </Card>
                  <Card className="border-border/70 bg-muted/10">
                    <CardHeader className="pb-3">
                      <CardTitle className="text-sm">
                        {t("memoryDebug.promptBlocks")}
                      </CardTitle>
                    </CardHeader>
                    <CardContent className="font-prose text-sm text-foreground/90">
                      {t("memoryDebug.blocksAssembled", {
                        count: fileContext.prompt_blocks.length,
                      })}
                    </CardContent>
                  </Card>
                </div>

                <Card className="border-border/70 bg-muted/10">
                  <CardHeader className="pb-3">
                    <CardTitle className="text-sm">
                      {t("memoryDebug.chunkSelectionTrace")}
                    </CardTitle>
                  </CardHeader>
                  <CardContent>
                    {fileContext.chunks.length ? (
                      <div className="space-y-3">
                        {fileContext.chunks.map((chunk) => {
                          const usedInLastPrompt =
                            selectedChunkIds.has(chunk.chunk_id) ||
                            selectedChunkIndexes.has(chunk.chunk_index);
                          return (
                            <div
                              key={`${chunk.chunk_id}-trace`}
                              className="rounded-lg border border-border/70 bg-background/60 p-4"
                            >
                              <div className="flex flex-wrap items-center gap-2 text-sm font-medium text-foreground/90">
                                <span>
                                  {t("memoryDebug.chunkNumber", {
                                    number: chunk.chunk_index + 1,
                                  })}
                                </span>
                                <Badge
                                  variant={usedInLastPrompt ? "default" : "outline"}
                                >
                                  {usedInLastPrompt
                                    ? t("memoryDebug.usedInLastPrompt")
                                    : t("memoryDebug.storedButSkipped")}
                                </Badge>
                                {chunk.line_start != null && chunk.line_end != null ? (
                                  <span className="text-xs text-muted-foreground">
                                    {t("memoryDebug.linesRange", {
                                      start: chunk.line_start,
                                      end: chunk.line_end,
                                    })}
                                  </span>
                                ) : null}
                                <span className="text-xs text-muted-foreground">
                                  {t("memoryDebug.approxTokens", {
                                    count: chunk.estimated_tokens,
                                  })}
                                </span>
                              </div>
                              <p className="font-prose mt-2 text-sm leading-7 text-muted-foreground">
                                {usedInLastPrompt
                                  ? t("memoryDebug.chunkSelectedForPrompt")
                                  : t("memoryDebug.chunkStoredNotNeeded")}
                              </p>
                            </div>
                          );
                        })}
                      </div>
                    ) : (
                      <div className="font-prose text-sm text-muted-foreground">
                        {t("memoryDebug.noChunkTrace")}
                      </div>
                    )}
                  </CardContent>
                </Card>

                <Card className="border-border/70 bg-muted/10">
                  <CardHeader className="pb-3">
                    <CardTitle className="text-sm">
                      {t("memoryDebug.promptAssemblyTrace")}
                    </CardTitle>
                  </CardHeader>
                  <CardContent>
                    {fileContext.prompt_blocks.length ? (
                      <div className="space-y-3">
                        {fileContext.prompt_blocks.map((block, index) => {
                          const details = getPromptBlockDetails(block);
                          return (
                            <div
                              key={`${selectedFile.id}-trace-block-${index}`}
                              className="rounded-lg border border-border/70 bg-background/60 p-4"
                            >
                              <div className="flex flex-wrap items-center gap-2">
                                <Badge variant="outline">{t(details.labelKey)}</Badge>
                                <span className="text-sm font-medium text-foreground/90">
                                  {t("memoryDebug.blockNumber", { number: index + 1 })}
                                </span>
                              </div>
                              <p className="font-prose mt-2 text-sm leading-7 text-muted-foreground">
                                {t(details.descriptionKey)}
                              </p>
                            </div>
                          );
                        })}
                      </div>
                    ) : (
                      <div className="font-prose text-sm text-muted-foreground">
                        {t("memoryDebug.noPromptAssemblyTrace")}
                      </div>
                    )}
                  </CardContent>
                </Card>
              </div>

              <div className="grid gap-4 xl:grid-cols-3">
                <Card className="border-border/70 bg-muted/10">
                  <CardHeader className="pb-3">
                    <CardTitle className="text-sm">
                      {t("memoryDebug.structure")}
                    </CardTitle>
                  </CardHeader>
                  <CardContent>
                    {fileContext.summary_structure.length ? (
                      <ul className="space-y-2 text-sm text-foreground/90">
                        {fileContext.summary_structure.map((line) => (
                          <li
                            key={line}
                            className="rounded-md border border-border/70 bg-background/60 px-3 py-2"
                          >
                            {line}
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <div className="font-prose text-sm text-muted-foreground">
                        {t("memoryDebug.noStructuralLines")}
                      </div>
                    )}
                  </CardContent>
                </Card>

                <Card className="border-border/70 bg-muted/10">
                  <CardHeader className="pb-3">
                    <CardTitle className="text-sm">
                      {t("memoryDebug.keyPoints")}
                    </CardTitle>
                  </CardHeader>
                  <CardContent>
                    {fileContext.summary_key_points.length ? (
                      <ul className="space-y-2 text-sm text-foreground/90">
                        {fileContext.summary_key_points.map((line) => (
                          <li
                            key={line}
                            className="rounded-md border border-border/70 bg-background/60 px-3 py-2"
                          >
                            {line}
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <div className="font-prose text-sm text-muted-foreground">
                        {t("memoryDebug.noKeyPoints")}
                      </div>
                    )}
                  </CardContent>
                </Card>

                <Card className="border-border/70 bg-muted/10">
                  <CardHeader className="pb-3">
                    <CardTitle className="text-sm">
                      {t("memoryDebug.warnings")}
                    </CardTitle>
                  </CardHeader>
                  <CardContent>
                    {fileContext.summary_warnings.length ? (
                      <ul className="space-y-2 text-sm text-foreground/90">
                        {fileContext.summary_warnings.map((line) => (
                          <li
                            key={line}
                            className="rounded-md border border-border/70 bg-background/60 px-3 py-2"
                          >
                            {line}
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <div className="font-prose text-sm text-muted-foreground">
                        {t("memoryDebug.noWarnings")}
                      </div>
                    )}
                  </CardContent>
                </Card>
              </div>

              <div className="space-y-3">
                <h3 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
                  {t("memoryDebug.storedChunks")}
                </h3>
                {fileContext.chunks.length ? (
                  <div className="space-y-3">
                    {fileContext.chunks.map((chunk) => (
                      <Card
                        key={chunk.chunk_id}
                        className="border-border/70 bg-muted/10"
                      >
                        <CardHeader className="pb-3">
                          <CardTitle className="flex flex-wrap items-center gap-2 text-sm">
                            <span>
                              {t("memoryDebug.chunkNumber", {
                                number: chunk.chunk_index + 1,
                              })}
                            </span>
                            {fileContext.last_usage?.selected_chunk_ids.includes(
                              chunk.chunk_id
                            ) ? (
                              <Badge variant="outline">
                                {t("memoryDebug.usedInLastPrompt")}
                              </Badge>
                            ) : null}
                            {chunk.line_start != null && chunk.line_end != null
                              ? ` · ${t("memoryDebug.linesRange", {
                                  start: chunk.line_start,
                                  end: chunk.line_end,
                                })}`
                              : ""}
                            {` · ${t("memoryDebug.approxTokens", {
                              count: chunk.estimated_tokens,
                            })}`}
                          </CardTitle>
                        </CardHeader>
                        <CardContent>
                          <pre className="overflow-x-auto whitespace-pre-wrap break-words rounded-md border border-border/70 bg-background/60 p-3 text-xs leading-6 text-foreground/90">
                            {chunk.text}
                          </pre>
                        </CardContent>
                      </Card>
                    ))}
                  </div>
                ) : (
                  <div className="font-prose text-sm text-muted-foreground">
                    {t("memoryDebug.noStoredChunks")}
                  </div>
                )}
              </div>
            </>
          ) : null}
        </CardContent>
      </Card>
    </div>
  );
}
