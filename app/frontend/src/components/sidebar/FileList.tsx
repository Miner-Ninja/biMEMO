import { useState, useEffect, useCallback, useRef, type MouseEvent } from 'react'
import { FileText, Trash2, Upload, Loader2, Check } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { UploadProgressPanel } from '@/components/ui/UploadProgressPanel'
import type { UploadProgressData } from '@/components/ui/UploadProgressPanel'
import { cn } from '@/lib/utils'
import { fetchFiles, deleteFileById, uploadFile } from '@/lib/bimemo-api'
import type { IndexedFile } from '@/lib/bimemo-api'
import { useConfirm } from '@/hooks/useConfirm'
import { FILE_INPUT_ACCEPT, isAllowedUploadFilename } from '@/lib/file-upload-contract'
import { FileListSkeleton } from '@/components/ui/skeletons'

const MAX_FILE_SIZE = 10 * 1024 * 1024 // 10MB

interface FileListProps {
  activeFiles: string[]
  onActiveFilesChange: (fileIds: string[]) => void
  onFilesChange?: () => void
  refreshKey?: number
  searchQuery?: string
}

export function FileList({ activeFiles, onActiveFilesChange, onFilesChange, refreshKey, searchQuery = '' }: FileListProps) {
  const [files, setFiles] = useState<IndexedFile[]>([])
  const [loading, setLoading] = useState(true)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [bulkDeleting, setBulkDeleting] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [uploadProgress, setUploadProgress] = useState<UploadProgressData | null>(null)
  const [dragOver, setDragOver] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const clearTimerRef = useRef<ReturnType<typeof setTimeout>>(null)
  const dragCounterRef = useRef(0)
  const { confirm, ConfirmDialog } = useConfirm()

  useEffect(() => {
    return () => {
      if (clearTimerRef.current) {
        clearTimeout(clearTimerRef.current)
      }
    }
  }, [])

  const loadFiles = useCallback(async () => {
    setLoading(true)
    try {
      const data = await fetchFiles()
      setFiles(data)
      const validIds = new Set(data.map((file) => file.file_id))
      onActiveFilesChange(activeFiles.filter((fileId) => validIds.has(fileId)))
      onFilesChange?.()
    } catch (err) {
      console.error('Failed to load files:', err)
    } finally {
      setLoading(false)
    }
  }, [activeFiles, onActiveFilesChange, onFilesChange])

  useEffect(() => {
    loadFiles()
  }, [loadFiles])

  // Re-fetch when parent signals new files (e.g. chat attachment upload)
  useEffect(() => {
    if (refreshKey) {
      loadFiles()
    }
  }, [refreshKey, loadFiles])

  const activeSet = new Set(activeFiles)

  const toggleActive = (fileId: string) => {
    if (activeSet.has(fileId)) {
      onActiveFilesChange(activeFiles.filter((f) => f !== fileId))
    } else {
      onActiveFilesChange([...activeFiles, fileId])
    }
  }

  const toggleAll = () => {
    if (activeSet.size === files.length) {
      onActiveFilesChange([])
    } else {
      onActiveFilesChange(files.map((f) => f.file_id))
    }
  }

  const handleDelete = async (fileId: string) => {
    setDeletingId(fileId)
    try {
      await deleteFileById(fileId)
      if (activeSet.has(fileId)) {
        onActiveFilesChange(activeFiles.filter((f) => f !== fileId))
      }
      await loadFiles()
      onFilesChange?.()
    } catch (err) {
      console.error('Failed to delete file:', err)
    } finally {
      setDeletingId(null)
    }
  }

  const handleDeleteSelected = async () => {
    const selectedFiles = files.filter((f) => activeSet.has(f.file_id))
    if (selectedFiles.length === 0) return

    setBulkDeleting(true)
    try {
      for (const file of selectedFiles) {
        await deleteFileById(file.file_id)
      }
      onActiveFilesChange([])
      await loadFiles()
      onFilesChange?.()
    } catch (err) {
      console.error('Failed to delete selected files:', err)
    } finally {
      setBulkDeleting(false)
    }
  }

  const handleUploadClick = () => {
    fileInputRef.current?.click()
  }

  const processFiles = async (fileList: File[]) => {
    setUploadError(null)
    setUploading(true)

    if (clearTimerRef.current) {
      clearTimeout(clearTimerRef.current)
      clearTimerRef.current = null
    }

    let validFiles: File[] = []
    const errors: string[] = []

    for (const file of fileList) {
      if (!isAllowedUploadFilename(file.name)) {
        errors.push(`Unsupported file type: ${file.name}`)
        continue
      }
      if (file.size > MAX_FILE_SIZE) {
        errors.push(`File too large: ${file.name} (max 10MB)`)
        continue
      }
      validFiles.push(file)
    }

    if (errors.length > 0) {
      setUploadError(errors.join('. '))
    }

    if (validFiles.length === 0) {
      setUploading(false)
      return
    }

    // Check for duplicate filenames and ask user to confirm replacement
    const existingNames = new Set(files.map((f) => f.filename))
    const confirmedFiles: File[] = []
    for (const file of validFiles) {
      if (existingNames.has(file.name)) {
        const shouldReplace = await confirm({
          title: 'File already exists',
          description: `"${file.name}" already exists. The old version will be replaced.`,
          confirmLabel: 'Replace',
          cancelLabel: 'Skip',
        })
        if (shouldReplace) {
          confirmedFiles.push(file)
        }
      } else {
        confirmedFiles.push(file)
      }
    }

    if (confirmedFiles.length === 0) {
      setUploading(false)
      return
    }

    validFiles = confirmedFiles

    const results: Array<{ name: string; ok: boolean }> = []

    setUploadProgress({
      current: 1,
      total: validFiles.length,
      filename: validFiles[0].name,
      results: [],
    })

    for (let i = 0; i < validFiles.length; i++) {
      const file = validFiles[i]

      setUploadProgress({
        current: i + 1,
        total: validFiles.length,
        filename: file.name,
        results: [...results],
      })

      try {
        await uploadFile(file)
        results.push({ name: file.name, ok: true })
      } catch {
        results.push({ name: file.name, ok: false })
      }
    }

    // Show final completed state
    setUploadProgress({
      current: validFiles.length,
      total: validFiles.length,
      filename: validFiles[validFiles.length - 1].name,
      results: [...results],
    })

    await loadFiles()
    onFilesChange?.()
    setUploading(false)

    clearTimerRef.current = setTimeout(() => {
      setUploadProgress(null)
      clearTimerRef.current = null
    }, 3000)
  }

  const handleFileSelected = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const selectedFiles = e.target.files
    if (!selectedFiles || selectedFiles.length === 0) return
    e.target.value = ''
    await processFiles(Array.from(selectedFiles))
  }

  const handleDragEnter = (e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    dragCounterRef.current++
    setDragOver(true)
  }

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
  }

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    dragCounterRef.current--
    if (dragCounterRef.current === 0) {
      setDragOver(false)
    }
  }

  const handleDrop = async (e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    dragCounterRef.current = 0
    setDragOver(false)

    const droppedFiles = Array.from(e.dataTransfer.files)
    if (droppedFiles.length === 0) return
    await processFiles(droppedFiles)
  }

  const dragHandlers = {
    onDragEnter: handleDragEnter,
    onDragOver: handleDragOver,
    onDragLeave: handleDragLeave,
    onDrop: handleDrop,
  }

  const progressPanel = uploadProgress && (
    <div className="mx-2">
      <UploadProgressPanel progress={uploadProgress} />
    </div>
  )

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
  )

  if (loading) {
    return (
      <>
        <ConfirmDialog />
        <FileListSkeleton />
      </>
    )
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
    )
  }

  if (files.length === 0) {
    // Empty state — prominent drag & drop zone
    return (
      <>
        <ConfirmDialog />
        <div className="h-full flex flex-col justify-center px-3">
          <div
            {...dragHandlers}
            onClick={handleUploadClick}
            className={cn(
              'flex flex-col items-center justify-center gap-3 rounded-xl border-2 border-dashed p-6 text-center cursor-pointer transition-colors',
              dragOver
                ? 'border-primary bg-primary/5'
                : 'border-muted-foreground/25 hover:border-muted-foreground/40'
            )}
          >
            <Upload className={cn(
              'h-10 w-10 transition-colors',
              dragOver ? 'text-primary' : 'text-muted-foreground/50'
            )} />
            <p className="text-sm text-muted-foreground">
              Drag files here or click to upload
            </p>
            <Button
              size="sm"
              className="gap-1.5"
              onClick={(e: MouseEvent<HTMLButtonElement>) => {
                e.stopPropagation()
                handleUploadClick()
              }}
            >
              <Upload className="h-4 w-4" />
              Upload files
            </Button>
            <p className="text-xs text-muted-foreground/60">
              PDF, DOCX, XLSX, CSV, TXT, JSON, Images
              <span className="mx-1.5">&middot;</span>
              Max 10 MB per file
            </p>
          </div>
          {uploadError && (
            <p className="mt-2 text-center text-xs text-destructive">{uploadError}</p>
          )}
          {hiddenInput}
        </div>
      </>
    )
  }

  const allActive = activeSet.size === files.length
  const filteredFiles = searchQuery
    ? files.filter((f) => f.filename.toLowerCase().includes(searchQuery.toLowerCase()))
    : files

  return (
    <div className="flex h-full flex-col" {...dragHandlers}>
      <ConfirmDialog />
      {/* Drag overlay */}
      {dragOver && (
        <div className="absolute inset-0 z-30 flex items-center justify-center rounded-lg border-2 border-dashed border-primary bg-primary/5 pointer-events-none">
          <div className="flex flex-col items-center gap-2 text-primary">
            <Upload className="h-10 w-10" />
            <p className="text-sm font-medium">Drop files to upload</p>
          </div>
        </div>
      )}

      {/* Header with select-all and upload */}
      <div className="flex items-center gap-2 px-3 py-2">
        <Button
          variant="ghost"
          size="sm"
          onClick={handleUploadClick}
          disabled={uploading}
          className="h-7 text-xs gap-1"
        >
          {uploading ? <Loader2 className="h-3 w-3 animate-spin" /> : <Upload className="h-3 w-3" />}
          Upload
        </Button>
        {activeSet.size > 0 && (
          <Button
            variant="ghost"
            size="sm"
            onClick={handleDeleteSelected}
            disabled={bulkDeleting}
            className="h-7 text-xs gap-1 text-destructive hover:text-destructive hover:bg-destructive/10"
          >
            {bulkDeleting ? <Loader2 className="h-3 w-3 animate-spin" /> : <Trash2 className="h-3 w-3" />}
            {activeSet.size}
          </Button>
        )}
        <div className="flex-1" />
        <button
          type="button"
          className="flex items-center gap-2 text-xs text-muted-foreground cursor-pointer select-none"
          onClick={toggleAll}
        >
          <span className={cn(
            'flex h-4 w-4 items-center justify-center rounded-sm border transition-colors',
            allActive
              ? 'border-primary bg-primary text-primary-foreground'
              : 'border-zinc-500 bg-zinc-700/50'
          )}>
            {allActive && <Check className="h-3 w-3" />}
          </span>
          All
        </button>
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
          <p className="px-3 py-4 text-center text-sm text-muted-foreground">
            No files found{searchQuery && <> for &ldquo;{searchQuery}&rdquo;</>}
          </p>
        ) : (
        <div className="grid grid-cols-3 gap-2">
          {filteredFiles.map((file) => {
            const isActive = activeSet.has(file.file_id)
            const isDeleting = deletingId === file.file_id
            return (
              <div
                key={file.file_id}
                className={cn(
                  'group relative flex flex-col items-center gap-1 rounded-lg p-2 cursor-pointer transition-all select-none',
                  isActive
                    ? 'bg-primary/10 ring-2 ring-primary'
                    : 'hover:bg-accent'
                )}
                onClick={() => toggleActive(file.file_id)}
              >
                {/* Active checkmark badge */}
                {isActive && (
                  <div className="absolute top-1 left-1 flex h-4 w-4 items-center justify-center rounded-full bg-primary text-primary-foreground">
                    <Check className="h-3 w-3" />
                  </div>
                )}

                {/* Hover controls overlay */}
                <div className="absolute inset-0 z-10 flex flex-col justify-between rounded-lg p-1 opacity-0 transition-opacity group-hover:opacity-100">
                  {/* Top row: trash (right-aligned) */}
                  <div className="flex items-start justify-end">
                    <button
                      className="flex h-5 w-5 items-center justify-center rounded text-muted-foreground hover:text-destructive transition-colors"
                      disabled={isDeleting}
                      onClick={(e) => {
                        e.stopPropagation()
                        handleDelete(file.file_id)
                      }}
                    >
                      {isDeleting
                        ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        : <Trash2 className="h-3.5 w-3.5" />}
                    </button>
                  </div>

                </div>

                {/* File icon */}
                <FileText className="h-8 w-8 text-muted-foreground group-hover:text-accent-foreground transition-colors" />

                {/* Filename */}
                <span className="text-xs text-center leading-tight line-clamp-2 w-full break-all">
                  {file.filename}
                </span>
              </div>
            )
          })}
        </div>
        )}
      </div>

      {/* Hidden file input */}
      {hiddenInput}
    </div>
  )
}
