import { useState, useEffect, useCallback, useRef, type MouseEvent } from "react";
import { useTranslation } from "react-i18next";
import { FileText, Trash2, Upload, Loader2, Check } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import { UploadProgressPanel } from "@/components/ui/UploadProgressPanel";
import type { UploadProgressData } from "@/components/ui/UploadProgressPanel";
import { cn } from "@/utils/utils";
import { fetchFiles, deleteFileById, uploadFile } from "@/services/bimemo-api";
import type { IndexedFile } from "@/services/bimemo-api";
import { useConfirm } from "@/hooks/useConfirm";
import {
  FILE_INPUT_ACCEPT,
  isAllowedUploadFilename,
} from "@/utils/file-upload-contract";
import { FileListSkeleton } from "@/components/ui/skeletons";
import { SidebarSectionLabel } from "./sidebar-chrome";

const MAX_FILE_SIZE = 10 * 1024 * 1024; // 10MB

interface FileListProps {
  activeFiles: string[];
  onActiveFilesChange: (fileIds: string[]) => void;
  onFilesChange?: (() => void) | undefined;
  refreshKey?: number | undefined;
  searchQuery?: string;
}

export function FileList({
  activeFiles,
  onActiveFilesChange,
  onFilesChange,
  refreshKey,
  searchQuery = "",
}: FileListProps) {
  const { t } = useTranslation();
  const [files, setFiles] = useState<IndexedFile[]>([]);
  const [loading, setLoading] = useState(true);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [bulkDeleting, setBulkDeleting] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploadProgress, setUploadProgress] = useState<UploadProgressData | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const clearTimerRef = useRef<ReturnType<typeof setTimeout>>(null);
  const dragCounterRef = useRef(0);
  const { confirm, ConfirmDialog } = useConfirm();

  useEffect(() => {
    return () => {
      if (clearTimerRef.current) {
        clearTimeout(clearTimerRef.current);
      }
    };
  }, []);

  const loadFiles = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchFiles();
      setFiles(data);
      const validIds = new Set(data.map((file) => file.file_id));
      onActiveFilesChange(activeFiles.filter((fileId) => validIds.has(fileId)));
      onFilesChange?.();
    } catch (err) {
      console.error("Failed to load files:", err);
    } finally {
      setLoading(false);
    }
  }, [activeFiles, onActiveFilesChange, onFilesChange]);

  useEffect(() => {
    loadFiles();
  }, [loadFiles]);

  // Re-fetch when parent signals new files (e.g. chat attachment upload)
  useEffect(() => {
    if (refreshKey) {
      loadFiles();
    }
  }, [refreshKey, loadFiles]);

  const activeSet = new Set(activeFiles);

  const toggleActive = (fileId: string) => {
    if (activeSet.has(fileId)) {
      onActiveFilesChange(activeFiles.filter((f) => f !== fileId));
    } else {
      onActiveFilesChange([...activeFiles, fileId]);
    }
  };

  const toggleAll = () => {
    if (activeSet.size === files.length) {
      onActiveFilesChange([]);
    } else {
      onActiveFilesChange(files.map((f) => f.file_id));
    }
  };

  const handleDelete = async (fileId: string) => {
    setDeletingId(fileId);
    try {
      await deleteFileById(fileId);
      if (activeSet.has(fileId)) {
        onActiveFilesChange(activeFiles.filter((f) => f !== fileId));
      }
      await loadFiles();
      onFilesChange?.();
    } catch (err) {
      console.error("Failed to delete file:", err);
    } finally {
      setDeletingId(null);
    }
  };

  const handleDeleteSelected = async () => {
    const selectedFiles = files.filter((f) => activeSet.has(f.file_id));
    if (selectedFiles.length === 0) return;

    setBulkDeleting(true);
    try {
      for (const file of selectedFiles) {
        await deleteFileById(file.file_id);
      }
      onActiveFilesChange([]);
      await loadFiles();
      onFilesChange?.();
    } catch (err) {
      console.error("Failed to delete selected files:", err);
    } finally {
      setBulkDeleting(false);
    }
  };

  const handleUploadClick = () => {
    fileInputRef.current?.click();
  };

  const processFiles = async (fileList: File[]) => {
    setUploadError(null);
    setUploading(true);

    if (clearTimerRef.current) {
      clearTimeout(clearTimerRef.current);
      clearTimerRef.current = null;
    }

    let validFiles: File[] = [];
    const errors: string[] = [];

    for (const file of fileList) {
      if (!isAllowedUploadFilename(file.name)) {
        errors.push(t("sidebar.unsupportedFileType", { name: file.name }));
        continue;
      }
      if (file.size > MAX_FILE_SIZE) {
        errors.push(t("sidebar.fileTooLarge", { name: file.name }));
        continue;
      }
      validFiles.push(file);
    }

    if (errors.length > 0) {
      setUploadError(errors.join(". "));
    }

    if (validFiles.length === 0) {
      setUploading(false);
      return;
    }

    // Check for duplicate filenames and ask user to confirm replacement
    const existingNames = new Set(files.map((f) => f.filename));
    const confirmedFiles: File[] = [];
    for (const file of validFiles) {
      if (existingNames.has(file.name)) {
        const shouldReplace = await confirm({
          title: t("sidebar.fileAlreadyExists"),
          description: t("sidebar.fileExistsDescription", { name: file.name }),
          confirmLabel: t("sidebar.replace"),
          cancelLabel: t("sidebar.skip"),
        });
        if (shouldReplace) {
          confirmedFiles.push(file);
        }
      } else {
        confirmedFiles.push(file);
      }
    }

    if (confirmedFiles.length === 0) {
      setUploading(false);
      return;
    }

    validFiles = confirmedFiles;

    const firstFile = validFiles[0];
    if (!firstFile) {
      setUploading(false);
      return;
    }

    const results: Array<{ name: string; ok: boolean }> = [];

    setUploadProgress({
      current: 1,
      total: validFiles.length,
      filename: firstFile.name,
      results: [],
    });

    for (let i = 0; i < validFiles.length; i++) {
      const file = validFiles[i];
      if (!file) continue;

      setUploadProgress({
        current: i + 1,
        total: validFiles.length,
        filename: file.name,
        results: [...results],
      });

      try {
        await uploadFile(file);
        results.push({ name: file.name, ok: true });
      } catch {
        results.push({ name: file.name, ok: false });
      }
    }

    // Show final completed state
    const lastFile = validFiles[validFiles.length - 1];
    setUploadProgress({
      current: validFiles.length,
      total: validFiles.length,
      filename: lastFile ? lastFile.name : "",
      results: [...results],
    });

    await loadFiles();
    onFilesChange?.();
    setUploading(false);

    clearTimerRef.current = setTimeout(() => {
      setUploadProgress(null);
      clearTimerRef.current = null;
    }, 3000);
  };

  const handleFileSelected = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const selectedFiles = e.target.files;
    if (!selectedFiles || selectedFiles.length === 0) return;
    e.target.value = "";
    await processFiles(Array.from(selectedFiles));
  };

  const handleDragEnter = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounterRef.current++;
    setDragOver(true);
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounterRef.current--;
    if (dragCounterRef.current === 0) {
      setDragOver(false);
    }
  };

  const handleDrop = async (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounterRef.current = 0;
    setDragOver(false);

    const droppedFiles = Array.from(e.dataTransfer.files);
    if (droppedFiles.length === 0) return;
    await processFiles(droppedFiles);
  };

  const dragHandlers = {
    onDragEnter: handleDragEnter,
    onDragOver: handleDragOver,
    onDragLeave: handleDragLeave,
    onDrop: handleDrop,
  };

  const progressPanel = uploadProgress && (
    <div className="mx-2">
      <UploadProgressPanel progress={uploadProgress} />
    </div>
  );

  // Hidden file input (shared across all states)
  const hiddenInput = (
    <input
      ref={fileInputRef}
      type="file"
      multiple
      accept={FILE_INPUT_ACCEPT}
      onChange={handleFileSelected}
      className="hidden"
    />
  );

  if (loading) {
    return (
      <>
        <ConfirmDialog />
        <FileListSkeleton />
      </>
    );
  }

  // Upload in progress — always centered vertically
  if (uploadProgress) {
    return (
      <>
        <ConfirmDialog />
        <div className="h-full flex flex-col justify-center">
          {progressPanel}
          {uploadError && (
            <div className="px-3">
              <p className="text-xs text-destructive">{uploadError}</p>
            </div>
          )}
          {hiddenInput}
        </div>
      </>
    );
  }

  if (files.length === 0) {
    return (
      <>
        <ConfirmDialog />
        <div className="h-full flex flex-col justify-center px-3">
          <Card
            {...dragHandlers}
            onClick={handleUploadClick}
            className={cn(
              "cursor-pointer border-2 border-dashed shadow-none transition-colors",
              dragOver
                ? "border-sidebar-primary bg-sidebar-primary/10"
                : "border-sidebar-border bg-sidebar-accent/20 hover:border-sidebar-foreground/20 hover:bg-sidebar-accent/30"
            )}
          >
            <CardContent className="p-0">
              <Empty className="gap-4 px-6 py-8 text-center">
                <EmptyHeader>
                  <EmptyMedia
                    className={cn(
                      "border-0 bg-transparent shadow-none",
                      dragOver ? "text-sidebar-primary" : "text-sidebar-foreground/45"
                    )}
                  >
                    <Upload />
                  </EmptyMedia>
                  <EmptyTitle>{t("sidebar.dragFilesHere")}</EmptyTitle>
                  <EmptyDescription>
                    {t("sidebar.supportedFormats")}
                    <span className="mx-1.5">&middot;</span>
                    {t("sidebar.maxFileSize")}
                  </EmptyDescription>
                </EmptyHeader>
                <EmptyContent>
                  <Button
                    size="sm"
                    className="gap-1.5"
                    onClick={(e: MouseEvent<HTMLButtonElement>) => {
                      e.stopPropagation();
                      handleUploadClick();
                    }}
                  >
                    <Upload />
                    {t("sidebar.uploadFiles")}
                  </Button>
                </EmptyContent>
              </Empty>
            </CardContent>
          </Card>
          {uploadError && (
            <p className="mt-2 text-center text-xs text-destructive">{uploadError}</p>
          )}
          {hiddenInput}
        </div>
      </>
    );
  }

  const allActive = activeSet.size === files.length;
  const filteredFiles = searchQuery
    ? files.filter((f) => f.filename.toLowerCase().includes(searchQuery.toLowerCase()))
    : files;

  return (
    <div className="flex h-full flex-col" {...dragHandlers}>
      <ConfirmDialog />
      {/* Drag overlay */}
      {dragOver && (
        <div className="pointer-events-none absolute inset-0 z-30 rounded-lg border-2 border-dashed border-primary bg-primary/5 p-3">
          <Card className="flex h-full items-center justify-center border-primary/20 bg-background/85 shadow-none">
            <CardContent className="flex flex-col items-center gap-2 p-6 text-sidebar-primary">
              <Upload className="h-10 w-10" />
              <p className="text-sm font-medium">{t("sidebar.dropFilesToUpload")}</p>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Header with select-all and upload */}
      <div className="flex items-center gap-2 px-3 py-2">
        <SidebarSectionLabel className="mr-auto">
          {t("sidebar.library")}
        </SidebarSectionLabel>
        <Button
          variant="ghost"
          size="sm"
          onClick={handleUploadClick}
          disabled={uploading}
          className="h-7 gap-1 text-xs text-sidebar-foreground/72 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
        >
          {uploading ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <Upload className="h-3 w-3" />
          )}
          {t("sidebar.upload")}
        </Button>
        {activeSet.size > 0 && (
          <Button
            variant="ghost"
            size="sm"
            onClick={handleDeleteSelected}
            disabled={bulkDeleting}
            className="h-7 gap-1 text-xs text-destructive hover:bg-destructive/10 hover:text-destructive"
          >
            {bulkDeleting ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <Trash2 className="h-3 w-3" />
            )}
            {activeSet.size}
          </Button>
        )}
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="h-7 gap-2 px-2 text-xs text-sidebar-foreground/72 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
          onClick={toggleAll}
        >
          <Badge
            variant={allActive ? "info" : "outline"}
            className="border-transparent px-1.5 py-0"
          >
            {allActive ? <Check className="h-3 w-3" /> : null}
          </Badge>
          {t("sidebar.all")}
        </Button>
      </div>

      {/* Upload error */}
      {uploadError && (
        <div className="px-3 pb-2">
          <p className="text-xs text-destructive">{uploadError}</p>
        </div>
      )}

      {/* File grid */}
      <div className="flex-1 min-h-0 overflow-y-auto p-2">
        {filteredFiles.length === 0 ? (
          <p className="px-3 py-4 text-center text-sm text-sidebar-foreground/55">
            {searchQuery
              ? t("sidebar.noFilesFoundFor", { query: searchQuery })
              : t("sidebar.noFilesFound")}
          </p>
        ) : (
          <div className="grid grid-cols-3 gap-2">
            {filteredFiles.map((file) => {
              const isActive = activeSet.has(file.file_id);
              const isDeleting = deletingId === file.file_id;
              return (
                <Card
                  key={file.file_id}
                  className={cn(
                    "group relative cursor-pointer select-none border-border/60 shadow-none transition-all",
                    isActive
                      ? "border-primary/40 bg-primary/10 ring-2 ring-primary/50"
                      : "hover:bg-accent/70"
                  )}
                  onClick={() => toggleActive(file.file_id)}
                >
                  {isActive && (
                    <Badge
                      variant="info"
                      className="absolute left-1 top-1 z-10 border-transparent px-1.5 py-0"
                    >
                      <Check className="h-3 w-3" />
                    </Badge>
                  )}

                  <CardContent className="flex flex-col items-center gap-2 p-2">
                    <div className="flex w-full justify-end">
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        className="z-10 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100"
                        disabled={isDeleting}
                        onClick={(e) => {
                          e.stopPropagation();
                          handleDelete(file.file_id);
                        }}
                        aria-label={t("sidebar.deleteFile", { name: file.filename })}
                      >
                        {isDeleting ? (
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        ) : (
                          <Trash2 className="h-3.5 w-3.5" />
                        )}
                      </Button>
                    </div>

                    <FileText className="h-8 w-8 text-muted-foreground transition-colors group-hover:text-accent-foreground" />

                    <span className="w-full break-all text-center text-xs leading-tight line-clamp-2">
                      {file.filename}
                    </span>
                  </CardContent>
                </Card>
              );
            })}
          </div>
        )}
      </div>

      {/* Hidden file input */}
      {hiddenInput}
    </div>
  );
}
