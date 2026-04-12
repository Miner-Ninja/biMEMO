import { useState, useMemo, memo, useEffect } from 'react'
import { ChevronRight, ChevronDown, AlertTriangle, BarChart3, Copy, Check, Pencil, X, Mic, Expand } from 'lucide-react'
import { SpeakButton } from './SpeakButton'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Badge } from '@/components/ui/badge'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Separator } from '@/components/ui/separator'
import { Textarea } from '@/components/ui/textarea'
import { DataChart } from './DataChart'
import { MarkdownCodeBlock } from './MarkdownCodeBlock'
import { ResultsTable } from './ResultsTable'
import { ThinkingBlock } from './ThinkingBlock'
import { TokenStats } from './TokenStats'
import { VoicePlayer } from './VoicePlayer'
import { detectChartType } from '@/lib/chart-utils'
import { buildMarkdownComponents } from '@/lib/markdownComponents'
import type { Message, PendingConfirmation } from '@/types/chat'
import ConfirmationCard from './ConfirmationCard'
import { cn, formatDate } from '@/lib/utils'

interface ChatMessageProps {
  message: Message
  assistantName?: string
  onCommandClick?: (command: string) => void
  showSql?: boolean
  showThinking?: boolean
  isEditable?: boolean
  onEdit?: (messageId: string, newContent: string) => void
  ttsAvailable?: boolean
  onConfirmationStatusChange?: (actionId: string, status: PendingConfirmation['status']) => void
}

/**
 * Single chat message with collapsible SQL and Results sections.
 * Memoized to prevent re-renders when other messages update.
 */
export const ChatMessage = memo(function ChatMessage({ message, assistantName, onCommandClick, showSql = true, showThinking = true, isEditable = false, onEdit, ttsAvailable = false, onConfirmationStatusChange }: ChatMessageProps) {
  const [sqlOpen, setSqlOpen] = useState(false)
  const [resultsOpen, setResultsOpen] = useState(false)
  const [errorDetailOpen, setErrorDetailOpen] = useState(false)
  const [copied, setCopied] = useState(false)
  const [showFallbackChart, setShowFallbackChart] = useState(false)
  const [isEditing, setIsEditing] = useState(false)
  const [editValue, setEditValue] = useState('')
  const [selectedImage, setSelectedImage] = useState<string | null>(null)

  const isUser = message.role === 'user'
  const hasError = Boolean(message.error)
  const hasSql = Boolean(message.sql)
  const hasResults = Boolean(message.results && message.results.data.length > 0)
  const hasStats = Boolean(message.stats)
  const hasThinking = Boolean(message.thinking)
  const hasChartBlock = message.content.includes('```chart')
  const hasImages = Boolean(message.images && message.images.length > 0)

  // Fallback chart detection from table results
  const fallbackChart = useMemo(() => {
    if (hasChartBlock || !hasResults || !message.results) return null
    return detectChartType(
      message.results.columns,
      message.results.data as Record<string, unknown>[],
    )
  }, [hasChartBlock, hasResults, message.results])

  const cleanContent = message.content
  const suggestions = message.suggestions ?? []

  const markdownComponents = useMemo(
    () => buildMarkdownComponents(onCommandClick),
    [onCommandClick],
  )

  const handleCopyContent = async () => {
    if (!cleanContent) return
    try {
      await navigator.clipboard.writeText(cleanContent)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch (error) {
      console.error('Failed to copy:', error)
    }
  }

  const handleStartEdit = () => {
    setEditValue(message.content)
    setIsEditing(true)
  }

  const handleCancelEdit = () => {
    setIsEditing(false)
    setEditValue('')
  }

  const handleSaveEdit = () => {
    const trimmed = editValue.trim()
    if (trimmed && trimmed !== message.content && onEdit) {
      onEdit(message.id, trimmed)
    }
    setIsEditing(false)
    setEditValue('')
  }

  useEffect(() => {
    if (!selectedImage) return undefined

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setSelectedImage(null)
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [selectedImage])

  return (
    <div
      className={cn(
        'relative group flex gap-2 rounded-xl px-3.5 py-3 sm:gap-3 sm:rounded-lg sm:px-4 sm:py-4',
        isUser ? 'bg-transparent' : 'bg-muted/50'
      )}
    >
      {/* Action buttons — top-right of the entire message row */}
      {cleanContent && !isEditing && (
        <div className="absolute right-2.5 top-2.5 z-10 flex items-center gap-0.5 opacity-100 transition-opacity sm:right-4 sm:top-4 sm:opacity-0 sm:group-hover:opacity-100">
          {ttsAvailable && !isUser && (
            <SpeakButton text={cleanContent} />
          )}
          <Button
            variant="ghost"
            size="icon"
            onClick={handleCopyContent}
            className="h-8 w-8"
            aria-label={copied ? 'Copied!' : 'Copy message'}
          >
            {copied ? (
              <Check className="h-4 w-4 text-primary" />
            ) : (
              <Copy className="h-4 w-4" />
            )}
          </Button>
        </div>
      )}

      {/* Edit button — bottom-right, only for the last user message */}
      {isEditable && !isEditing && (
        <div className="absolute bottom-2.5 right-2.5 z-10 opacity-100 transition-opacity sm:bottom-4 sm:right-4 sm:opacity-0 sm:group-hover:opacity-100">
          <Button
            variant="ghost"
            size="icon"
            onClick={handleStartEdit}
            className="h-8 w-8"
            aria-label="Edit message"
          >
            <Pencil className="h-4 w-4" />
          </Button>
        </div>
      )}

      {/* Avatar */}
      <Avatar className="mt-0.5 h-7 w-7 shrink-0 sm:h-8 sm:w-8">
        {isUser ? (
          <>
            <AvatarImage src="/avatars/user" alt="User avatar" className="object-cover" />
            <AvatarFallback className="bg-primary text-primary-foreground">U</AvatarFallback>
          </>
        ) : (
          <>
            <AvatarImage src="/avatars/ai" alt="AI avatar" className="object-cover" />
            <AvatarFallback className="bg-muted">
              <img src="/bimemo_mini.webp" alt="biMEMO AI avatar" className="h-full w-full object-cover" />
            </AvatarFallback>
          </>
        )}
      </Avatar>

      {/* Content */}
      <div className="flex-1 min-w-0 space-y-2.5 pr-4 sm:space-y-3 sm:pr-9">
        {/* Header with role and timestamp */}
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 pr-16 sm:pr-20">
          <span className="text-sm font-medium sm:text-base">{isUser ? 'You' : (assistantName || 'Assistant')}</span>
          {message.isVoice && (
            <Badge variant="info" className="gap-1">
              <Mic className="h-3 w-3" />
              voice
            </Badge>
          )}
          <span className="text-xs text-muted-foreground">{formatDate(message.timestamp)}</span>
        </div>

        {/* Voice player for voice messages */}
        {message.voiceDataUrl && (
          <VoicePlayer src={message.voiceDataUrl} />
        )}

        {/* Attached images */}
        {hasImages && (
          <div className="grid max-w-3xl grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {message.images!.map((imageUrl, imageIndex) => (
              <Card
                key={`${message.id}-image-${imageIndex}`}
                className="group/image overflow-hidden border-border/70 bg-card shadow-none"
              >
                <button
                  type="button"
                  className="w-full text-left"
                  onClick={() => setSelectedImage(imageUrl)}
                  aria-label={`Open image ${imageIndex + 1}`}
                >
                  <img
                    src={imageUrl}
                    alt={`Attached image ${imageIndex + 1}`}
                    className="h-40 w-full object-cover transition-transform duration-200 group-hover/image:scale-[1.02]"
                    loading="lazy"
                  />
                  <CardContent className="flex items-center justify-between gap-2 px-3 py-2">
                    <span className="text-xs text-muted-foreground">{`Image ${imageIndex + 1}`}</span>
                    <Badge variant="outline" className="gap-1 border-border/70 px-2 py-0.5">
                      <Expand className="h-3 w-3" />
                      Open
                    </Badge>
                  </CardContent>
                </button>
              </Card>
            ))}
          </div>
        )}

        {/* Pending RED zone confirmations */}
        {message.pendingConfirmations && message.pendingConfirmations.length > 0 && (
          <div className="max-w-md">
            {message.pendingConfirmations.map((c) => (
              <ConfirmationCard
                key={c.action_id}
                confirmation={c}
                onStatusChange={onConfirmationStatusChange || (() => {})}
              />
            ))}
          </div>
        )}

        {/* Token stats for assistant messages — reserve space to prevent layout shift */}
        {message.role === 'assistant' && (
          <div className="min-h-[14px]">
            {hasStats && (
              <div className="animate-in fade-in duration-500">
                <TokenStats stats={message.stats!} stageTimes={message.stageTimes} />
              </div>
            )}
          </div>
        )}

        {/* Thinking block (collapsible) */}
        {hasThinking && showThinking && <ThinkingBlock thinking={message.thinking!} />}

        {/* Error message — structured or simple */}
        {hasError && (
          <Card className="border-destructive/20 bg-destructive/5 shadow-none">
            <CardContent className="flex flex-col gap-3 p-4">
              <div className="flex items-start gap-2 text-destructive">
              <AlertTriangle className="h-5 w-5 shrink-0 mt-0.5" />
              <div className="flex flex-col gap-1">
                <p className="text-sm font-medium">{message.error}</p>
                {message.errorSuggestion && (
                  <p className="text-sm text-muted-foreground">{message.errorSuggestion}</p>
                )}
              </div>
              </div>
              {message.errorDetail && (
                <>
                  <Separator />
                  <div className="flex flex-col gap-2">
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      className="w-fit px-0 text-xs text-muted-foreground hover:bg-transparent"
                      onClick={() => setErrorDetailOpen(!errorDetailOpen)}
                    >
                      {errorDetailOpen ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
                      Technical information
                    </Button>
                    {errorDetailOpen && (
                      <pre className="overflow-x-auto whitespace-pre-wrap rounded-lg bg-muted p-3 text-xs text-muted-foreground">
                        {message.errorDetail}
                      </pre>
                    )}
                  </div>
                </>
              )}
            </CardContent>
          </Card>
        )}

        {/* Main content — inline edit mode or markdown */}
        {isEditing ? (
          <div className="space-y-2">
            <Textarea
              value={editValue}
              onChange={(e) => setEditValue(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  handleSaveEdit()
                } else if (e.key === 'Escape') {
                  handleCancelEdit()
                }
              }}
              className="min-h-20"
              autoFocus
            />
            <div className="flex gap-2">
              <Button variant="default" size="sm" onClick={handleSaveEdit} className="gap-1">
                <Check className="h-3.5 w-3.5" />
                Save & Submit
              </Button>
              <Button variant="ghost" size="sm" onClick={handleCancelEdit} className="gap-1">
                <X className="h-3.5 w-3.5" />
                Cancel
              </Button>
            </div>
          </div>
        ) : cleanContent ? (
          <div>
            <div className="markdown-content prose prose-sm prose-headings:text-foreground prose-p:text-foreground prose-strong:text-foreground prose-code:text-foreground prose-pre:bg-muted prose-pre:text-foreground prose-th:text-foreground prose-td:text-foreground prose-a:text-primary max-w-none text-sm sm:text-base">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                disallowedElements={['script', 'iframe', 'object', 'embed', 'form', 'style', 'link']}
                unwrapDisallowed={true}
                components={markdownComponents}
              >
                {cleanContent}
              </ReactMarkdown>
            </div>
            {/* Clickable suggestion chips */}
            {suggestions.length > 0 && onCommandClick && (
              <div className="mt-6 flex flex-col items-stretch gap-2 sm:mt-8 sm:items-start sm:gap-2">
                <Separator />
                <span className="mb-1 text-[10px] font-medium text-muted-foreground sm:text-xs">Suggestions</span>
                {suggestions.map((s, i) => (
                  <Button
                    key={i}
                    type="button"
                    variant="outline"
                    className="h-auto justify-start whitespace-normal rounded-xl border-primary/30 bg-primary/10 px-3.5 py-2.5 text-left text-[13px] leading-5 text-primary hover:bg-primary/15 sm:rounded-full sm:px-3 sm:py-1 sm:text-sm"
                    onClick={() => onCommandClick(s)}
                  >
                    {s}
                  </Button>
                ))}
              </div>
            )}
          </div>
        ) : null}

        {/* SQL Query (collapsible, hidden when showSql=false) */}
        {hasSql && showSql && message.sql && (
          <Collapsible open={sqlOpen} onOpenChange={setSqlOpen}>
            <CollapsibleTrigger className="flex w-full items-center gap-2 rounded border border-border p-2 text-sm hover:bg-muted/50">
              <ChevronRight
                className={cn('h-4 w-4 transition-transform', sqlOpen && 'rotate-90')}
              />
              <span>SQL Query</span>
            </CollapsibleTrigger>
            <CollapsibleContent className="mt-2">
              <MarkdownCodeBlock code={message.sql} language="sql" />
            </CollapsibleContent>
          </Collapsible>
        )}

        {/* Results (collapsible) */}
        {hasResults && (
          <>
            <Collapsible open={resultsOpen} onOpenChange={setResultsOpen}>
              <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                <CollapsibleTrigger className="flex flex-1 items-center gap-2 rounded border border-border p-2 text-sm hover:bg-muted/50">
                  <ChevronRight
                    className={cn('h-4 w-4 transition-transform', resultsOpen && 'rotate-90')}
                  />
                  <span>Results ({message.results!.rowCount} rows)</span>
                </CollapsibleTrigger>
                {fallbackChart && !showFallbackChart && (
                  <Button
                    variant="outline"
                    size="sm"
                    className="gap-1.5 self-start sm:shrink-0"
                    onClick={() => setShowFallbackChart(true)}
                  >
                    <BarChart3 className="h-3.5 w-3.5" />
                    Chart
                  </Button>
                )}
              </div>
              <CollapsibleContent className="mt-2">
                <ResultsTable results={message.results!} />
              </CollapsibleContent>
            </Collapsible>
            {showFallbackChart && fallbackChart && (
              <DataChart chart={fallbackChart} />
            )}
          </>
        )}
      </div>

      <Dialog open={Boolean(selectedImage)} onOpenChange={(open) => !open && setSelectedImage(null)}>
        <DialogContent className="max-w-[min(96vw,72rem)] border-none bg-transparent p-0 shadow-none">
          <DialogHeader className="sr-only">
            <DialogTitle>Image preview</DialogTitle>
          </DialogHeader>
          {selectedImage ? (
            <img
              src={selectedImage}
              alt="Selected attachment preview"
              className="max-h-[90vh] max-w-[90vw] rounded-lg object-contain shadow-2xl"
            />
          ) : null}
        </DialogContent>
      </Dialog>
    </div>
  )
})
