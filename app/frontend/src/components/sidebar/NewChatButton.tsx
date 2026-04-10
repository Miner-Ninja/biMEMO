import { Plus } from 'lucide-react'
import { Button } from '@/components/ui/button'

interface NewChatButtonProps {
  onClick: () => void
}

/**
 * Button to create a new chat
 */
export function NewChatButton({ onClick }: NewChatButtonProps) {
  return (
    <Button
      variant="outline"
      className="h-11 w-full justify-start gap-2"
      onClick={onClick}
    >
      <Plus className="h-4 w-4" />
      New Chat
    </Button>
  )
}
