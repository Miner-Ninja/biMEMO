import { useEffect, useState } from "react";

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
  if (!indexes.length) return "No chunks were selected.";
  return indexes.map((index) => String(index + 1)).join(", ");
}

function getPromptBlockDetails(block: string): { label: string; description: string } {
  if (block.includes(CHUNK_BLOCK_MARKER)) {
    return {
      label: "Selected chunks",
      description:
        "This block contains the chunk excerpts that were chosen for the last prompt build.",
    };
  }
  if (block.includes(SUMMARY_BLOCK_MARKER)) {
    return {
      label: "Stored summary",
      description:
        "This block comes from the saved file summary that the system keeps for large files.",
    };
  }
  if (block.includes(PREVIEW_BLOCK_MARKER)) {
    return {
      label: "File preview",
      description:
        "This block shows a shortened preview because the full file was not injected into the prompt.",
    };
  }
  if (block.includes(FULL_CONTENT_BLOCK_MARKER)) {
    return {
      label: "Inline file content",
      description:
        "This block contains the full file body because the file was small enough to inline safely.",
    };
  }
  if (block.includes(FILE_METADATA_BLOCK_MARKER)) {
    return {
      label: "File metadata",
      description:
        "This block describes the attached file itself, including its name and content type.",
    };
  }
  return {
    label: "Prompt context",
    description:
      "This block is part of the assembled file context, but it did not match a more specific trace category.",
  };
}

export function FileContextPanel({ refreshToken }: { refreshToken: number }) {
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
          if (!nextFiles.length) return "";
          return nextFiles.some((entry) => entry.id === current)
            ? current
            : nextFiles[0].id;
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
        File context index could not be loaded: {filesError}
      </div>
    );
  }

  if (!files.length) {
    return (
      <EmptyStateCard
        title="No file context yet"
        description="Upload one or more files through chat or the files route to inspect their stored summary and chunk context here."
      />
    );
  }

  const selectedFile = files.find((entry) => entry.id === selectedFileId) || files[0];
  const selectedChunkIds = new Set(fileContext?.last_usage?.selected_chunk_ids || []);
  const selectedChunkIndexes = new Set(
    fileContext?.last_usage?.selected_chunk_indexes || []
  );

  return (
    <div className="space-y-6">
      <SectionIntro
        title="File context"
        description="Inspect the saved summary, file kind, processing mode, and stored chunks that the chat route reuses for attached files."
      />

      <Card>
        <CardHeader>
          <CardTitle>Attached files</CardTitle>
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
            <span>MIME: {selectedFile.mime_type}</span>
            <span>Size: {selectedFile.size} bytes</span>
            <span>Uploaded: {formatIsoTimestamp(selectedFile.created_at)}</span>
            {fileContext ? <span>Kind: {fileContext.file_kind}</span> : null}
            {fileContext ? (
              <span>Estimated tokens: {fileContext.estimated_tokens}</span>
            ) : null}
          </div>
        </CardHeader>
        <CardContent className="space-y-6">
          {contextError ? (
            <div className="text-sm text-red-400">
              Stored file context could not be loaded: {contextError}
            </div>
          ) : null}

          {!contextError && !fileContext ? (
            <div className="text-sm text-muted-foreground">
              This file does not currently have stored text context. That usually means
              it is not treated as a text-like file.
            </div>
          ) : null}

          {fileContext ? (
            <>
              <div className="space-y-3">
                <h3 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
                  Stored mode reason
                </h3>
                <div className="rounded-lg border border-border bg-muted/10 p-4 text-sm leading-7">
                  {fileContext.processing_reason ||
                    "No stored mode explanation is available for this file yet."}
                </div>
              </div>

              <div className="space-y-3">
                <h3 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
                  Summary overview
                </h3>
                <div className="rounded-lg border border-border bg-muted/10 p-4 text-sm leading-7">
                  {fileContext.summary_overview || "No summary overview stored."}
                </div>
              </div>

              <Card className="border-border/70 bg-muted/10">
                <CardHeader className="pb-3">
                  <CardTitle className="text-sm">Last prompt usage</CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                  {fileContext.last_usage ? (
                    <>
                      <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
                        <span>Mode: {fileContext.last_usage.processing_mode}</span>
                        <span>
                          Used: {formatIsoTimestamp(fileContext.last_usage.used_at)}
                        </span>
                        <span>
                          Prompt chunks:{" "}
                          {formatChunkIndexes(
                            fileContext.last_usage.selected_chunk_indexes
                          )}
                        </span>
                      </div>
                      <div className="space-y-2">
                        <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                          Why this mode was used
                        </h4>
                        <div className="rounded-md border border-border/70 bg-background/60 p-3 text-sm leading-7">
                          {fileContext.last_usage.mode_reason ||
                            "No mode explanation stored for the last prompt use."}
                        </div>
                      </div>
                      <div className="space-y-2">
                        <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                          Last query
                        </h4>
                        <div className="rounded-md border border-border/70 bg-background/60 p-3 text-sm leading-7">
                          {fileContext.last_usage.user_query ||
                            "No query text was stored for the last usage snapshot."}
                        </div>
                      </div>
                    </>
                  ) : (
                    <div className="text-sm text-muted-foreground">
                      This file has not been used in a chat request since file-context
                      usage tracking was added.
                    </div>
                  )}
                </CardContent>
              </Card>

              <div className="space-y-3">
                <h3 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
                  Raw prompt blocks preview
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
                            Prompt block {index + 1}
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
                  <div className="text-sm text-muted-foreground">
                    No prompt preview is available yet for this file context.
                  </div>
                )}
              </div>

              <div className="space-y-3">
                <h3 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
                  Forensic view
                </h3>
                <div className="grid gap-4 xl:grid-cols-4">
                  <Card className="border-border/70 bg-muted/10">
                    <CardHeader className="pb-3">
                      <CardTitle className="text-sm">Stored mode</CardTitle>
                    </CardHeader>
                    <CardContent className="text-sm text-foreground/90">
                      {fileContext.processing_mode}
                    </CardContent>
                  </Card>
                  <Card className="border-border/70 bg-muted/10">
                    <CardHeader className="pb-3">
                      <CardTitle className="text-sm">Last prompt mode</CardTitle>
                    </CardHeader>
                    <CardContent className="text-sm text-foreground/90">
                      {fileContext.last_usage?.processing_mode ||
                        "This file has not been used in a prompt yet."}
                    </CardContent>
                  </Card>
                  <Card className="border-border/70 bg-muted/10">
                    <CardHeader className="pb-3">
                      <CardTitle className="text-sm">Chunk selection</CardTitle>
                    </CardHeader>
                    <CardContent className="text-sm text-foreground/90">
                      {selectedChunkIds.size} of {fileContext.chunks.length} stored
                      chunks were used in the last prompt.
                    </CardContent>
                  </Card>
                  <Card className="border-border/70 bg-muted/10">
                    <CardHeader className="pb-3">
                      <CardTitle className="text-sm">Prompt blocks</CardTitle>
                    </CardHeader>
                    <CardContent className="text-sm text-foreground/90">
                      {fileContext.prompt_blocks.length} blocks were assembled for the
                      current preview.
                    </CardContent>
                  </Card>
                </div>

                <Card className="border-border/70 bg-muted/10">
                  <CardHeader className="pb-3">
                    <CardTitle className="text-sm">Chunk selection trace</CardTitle>
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
                                <span>Chunk {chunk.chunk_index + 1}</span>
                                <Badge
                                  variant={usedInLastPrompt ? "default" : "outline"}
                                >
                                  {usedInLastPrompt
                                    ? "used in last prompt"
                                    : "stored but skipped"}
                                </Badge>
                                {chunk.line_start != null && chunk.line_end != null ? (
                                  <span className="text-xs text-muted-foreground">
                                    lines {chunk.line_start}-{chunk.line_end}
                                  </span>
                                ) : null}
                                <span className="text-xs text-muted-foreground">
                                  ~{chunk.estimated_tokens} tokens
                                </span>
                              </div>
                              <p className="mt-2 text-sm leading-7 text-muted-foreground">
                                {usedInLastPrompt
                                  ? "This chunk was selected for the most recent prompt assembly."
                                  : "This chunk is stored for recall, but it was not needed in the most recent prompt."}
                              </p>
                            </div>
                          );
                        })}
                      </div>
                    ) : (
                      <div className="text-sm text-muted-foreground">
                        This file does not have stored chunks, so there is no
                        chunk-selection trace to inspect.
                      </div>
                    )}
                  </CardContent>
                </Card>

                <Card className="border-border/70 bg-muted/10">
                  <CardHeader className="pb-3">
                    <CardTitle className="text-sm">Prompt assembly trace</CardTitle>
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
                                <Badge variant="outline">{details.label}</Badge>
                                <span className="text-sm font-medium text-foreground/90">
                                  Block {index + 1}
                                </span>
                              </div>
                              <p className="mt-2 text-sm leading-7 text-muted-foreground">
                                {details.description}
                              </p>
                            </div>
                          );
                        })}
                      </div>
                    ) : (
                      <div className="text-sm text-muted-foreground">
                        No prompt blocks are available, so there is no prompt-assembly
                        trace to inspect yet.
                      </div>
                    )}
                  </CardContent>
                </Card>
              </div>

              <div className="grid gap-4 xl:grid-cols-3">
                <Card className="border-border/70 bg-muted/10">
                  <CardHeader className="pb-3">
                    <CardTitle className="text-sm">Structure</CardTitle>
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
                      <div className="text-sm text-muted-foreground">
                        No structural lines were stored for this file.
                      </div>
                    )}
                  </CardContent>
                </Card>

                <Card className="border-border/70 bg-muted/10">
                  <CardHeader className="pb-3">
                    <CardTitle className="text-sm">Key points</CardTitle>
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
                      <div className="text-sm text-muted-foreground">
                        No key points were stored for this file.
                      </div>
                    )}
                  </CardContent>
                </Card>

                <Card className="border-border/70 bg-muted/10">
                  <CardHeader className="pb-3">
                    <CardTitle className="text-sm">Warnings</CardTitle>
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
                      <div className="text-sm text-muted-foreground">
                        No warnings were stored for this file.
                      </div>
                    )}
                  </CardContent>
                </Card>
              </div>

              <div className="space-y-3">
                <h3 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
                  Stored chunks
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
                            <span>Chunk {chunk.chunk_index + 1}</span>
                            {fileContext.last_usage?.selected_chunk_ids.includes(
                              chunk.chunk_id
                            ) ? (
                              <Badge variant="outline">used in last prompt</Badge>
                            ) : null}
                            {chunk.line_start != null && chunk.line_end != null
                              ? ` · lines ${chunk.line_start}-${chunk.line_end}`
                              : ""}
                            {` · ~${chunk.estimated_tokens} tokens`}
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
                  <div className="text-sm text-muted-foreground">
                    No stored chunks were needed for this file.
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
