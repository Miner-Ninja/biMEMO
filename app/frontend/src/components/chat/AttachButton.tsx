import { useRef, useState, useEffect } from 'react'
import { Paperclip, AlertCircle } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { FILE_INPUT_ACCEPT, isAllowedUploadFilename } from '@/lib/file-upload-contract'

const MAX_FILE_SIZE = 10 * 1024 * 1024 // 10MB

interface AttachButtonProps {
  onFileSelect: (files: File[]) => void
  disabled?: boolean
  className?: string
}

/**
 * Button to trigger file attachment
 */
export function AttachButton({ onFileSelect, disabled, className }: AttachButtonProps) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [error, setError] = useState<string | null>(null)

  // Auto-clear error after 3 seconds
  useEffect(() => {
    if (error) {
      const timer = setTimeout(() => setError(null), 3000)
      return () => clearTimeout(timer)
    }
  }, [error])

  const handleClick = () => {
    inputRef.current?.click()
  }

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const fileList = e.target.files
    if (!fileList || fileList.length === 0) return

    const validFiles: File[] = []
    for (const file of Array.from(fileList)) {
      if (!isAllowedUploadFilename(file.name)) {
        setError('This file type is not supported.')
        e.target.value = ''
        return
      }
      if (file.size > MAX_FILE_SIZE) {
        setError('File size must be less than 10MB.')
        e.target.value = ''
        return
      }
      validFiles.push(file)
    }

    setError(null)
    onFileSelect(validFiles)
    // Reset input so same files can be selected again
    e.target.value = ''
  }

  return (
    <div className="relative">
      <input
        ref={inputRef}
        type="file"
        accept={FILE_INPUT_ACCEPT}
        multiple
        onChange={handleChange}
        className="hidden"
      />
      <Button
        type="button"
        variant="ghost"
        size="icon"
        onClick={handleClick}
        disabled={disabled}
        aria-label="Attach file"
        className={cn("h-11 w-11 shrink-0", className)}
      >
        <Paperclip className="h-4 w-4" />
      </Button>
      {error && (
        <div className="absolute bottom-full left-0 mb-2 flex items-center gap-1 rounded bg-destructive px-2 py-1 text-xs text-destructive-foreground whitespace-nowrap">
          <AlertCircle className="h-3 w-3" />
          {error}
        </div>
      )}
    </div>
  )
}
