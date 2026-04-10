import { Skeleton } from './skeleton'

/**
 * Skeleton for a single chat item in the sidebar.
 */
export function ChatItemSkeleton() {
  return (
    <div className="flex items-center gap-2 px-3 py-2.5">
      <Skeleton className="h-4 w-4 shrink-0 rounded" />
      <div className="flex-1 space-y-1.5">
        <Skeleton className="h-3.5 w-3/4" />
        <Skeleton className="h-2.5 w-1/2" />
      </div>
    </div>
  )
}

/**
 * Skeleton for the chat list in the sidebar.
 */
export function ChatListSkeleton({ count = 6 }: { count?: number }) {
  return (
    <div className="space-y-1 px-1 py-2">
      {Array.from({ length: count }, (_, i) => (
        <ChatItemSkeleton key={i} />
      ))}
    </div>
  )
}

/**
 * Skeleton for a single message bubble.
 */
export function MessageSkeleton({ isUser = false }: { isUser?: boolean }) {
  return (
    <div className={`flex gap-3 px-4 py-4 ${isUser ? '' : 'bg-muted/50'}`}>
      <Skeleton className="h-8 w-8 shrink-0 rounded-full" />
      <div className="flex-1 space-y-2.5 pt-1">
        <Skeleton className="h-3 w-20" />
        {isUser ? (
          <Skeleton className="h-4 w-2/3" />
        ) : (
          <>
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-5/6" />
            <Skeleton className="h-4 w-3/4" />
          </>
        )}
      </div>
    </div>
  )
}

/**
 * Skeleton for the message list area.
 */
export function MessageListSkeleton() {
  return (
    <div className="flex-1 space-y-1 overflow-hidden px-2 py-4">
      <MessageSkeleton isUser />
      <MessageSkeleton />
      <MessageSkeleton isUser />
      <MessageSkeleton />
    </div>
  )
}

/**
 * Skeleton for file list in the sidebar.
 */
export function FileListSkeleton({ count = 4 }: { count?: number }) {
  return (
    <div className="space-y-2 px-3 py-2">
      {Array.from({ length: count }, (_, i) => (
        <div key={i} className="flex items-center gap-2">
          <Skeleton className="h-4 w-4 shrink-0" />
          <Skeleton className="h-3.5 w-full" />
        </div>
      ))}
    </div>
  )
}

/**
 * Skeleton for admin page content (cards/list items).
 */
export function AdminCardSkeleton({ count = 3 }: { count?: number }) {
  return (
    <div className="space-y-3">
      {Array.from({ length: count }, (_, i) => (
        <div key={i} className="rounded-lg border border-border p-4 space-y-3">
          <div className="flex items-center gap-3">
            <Skeleton className="h-5 w-5 rounded" />
            <Skeleton className="h-4 w-1/3" />
          </div>
          <Skeleton className="h-3 w-2/3" />
        </div>
      ))}
    </div>
  )
}

/**
 * Skeleton for the full app layout (sidebar + content).
 */
export function AppSkeleton() {
  return (
    <div className="flex h-dvh w-full bg-background">
      {/* Sidebar skeleton */}
      <div className="hidden w-72 flex-col border-r border-border bg-sidebar md:flex">
        <div className="flex h-14 items-center gap-2 px-4">
          <Skeleton className="h-5 w-5 rounded" />
          <Skeleton className="h-4 w-24" />
        </div>
        <div className="px-3 py-2">
          <Skeleton className="h-8 w-full rounded-md" />
        </div>
        <ChatListSkeleton />
      </div>
      {/* Main content skeleton */}
      <div className="flex flex-1 flex-col">
        <div className="flex h-12 items-center border-b border-border px-4">
          <Skeleton className="h-4 w-32" />
        </div>
        <MessageListSkeleton />
        <div className="border-t border-border p-4">
          <Skeleton className="h-12 w-full rounded-xl" />
        </div>
      </div>
    </div>
  )
}

/**
 * Skeleton for profile/settings form.
 */
export function FormSkeleton({ fields = 4 }: { fields?: number }) {
  return (
    <div className="space-y-4">
      {Array.from({ length: fields }, (_, i) => (
        <div key={i} className="space-y-2">
          <Skeleton className="h-3.5 w-24" />
          <Skeleton className="h-9 w-full rounded-md" />
        </div>
      ))}
    </div>
  )
}

/**
 * Skeleton for memory debug panels.
 */
export function PanelSkeleton() {
  return (
    <div className="space-y-3 p-4">
      <Skeleton className="h-4 w-40" />
      <Skeleton className="h-20 w-full rounded-lg" />
      <Skeleton className="h-20 w-full rounded-lg" />
    </div>
  )
}
