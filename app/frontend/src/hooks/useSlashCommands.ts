import { useState, type RefObject, type KeyboardEvent } from 'react'

export interface SlashCommand {
  name: string
  description: string
  hasArgs: boolean
}

export const SLASH_COMMANDS: SlashCommand[] = [
  { name: '/memories',   description: 'Show saved memories',    hasArgs: false },
  { name: '/forget',     description: 'Delete memory by ID',    hasArgs: true  },
  { name: '/forget_all', description: 'Clear all memories',     hasArgs: false },
  { name: '/schema',     description: 'Show database schema',   hasArgs: true  },
  { name: '/examples',   description: 'Show SQL examples',      hasArgs: true  },
  { name: '/help',       description: 'Show available commands', hasArgs: false },
]

interface UseSlashCommandsParams {
  setValue: (value: string) => void
  textareaRef: RefObject<HTMLTextAreaElement | null>
}

export function useSlashCommands({ setValue, textareaRef }: UseSlashCommandsParams) {
  const [showCommands, setShowCommands] = useState(false)
  const [filteredCommands, setFilteredCommands] = useState<SlashCommand[]>([])
  const [selectedIndex, setSelectedIndex] = useState(0)

  const handleSelectCommand = (cmd: SlashCommand) => {
    const newValue = cmd.hasArgs ? cmd.name + ' ' : cmd.name
    setValue(newValue)
    setShowCommands(false)
    requestAnimationFrame(() => {
      if (textareaRef.current) {
        textareaRef.current.focus()
        textareaRef.current.setSelectionRange(newValue.length, newValue.length)
      }
    })
  }

  const handleSlashFilter = (newValue: string) => {
    if (newValue.startsWith('/')) {
      const query = newValue.slice(1).toLowerCase()
      const matches = SLASH_COMMANDS.filter((cmd) =>
        cmd.name.slice(1).toLowerCase().startsWith(query)
      )
      if (matches.length > 0) {
        setFilteredCommands(matches)
        setSelectedIndex(0)
        setShowCommands(true)
      } else {
        setShowCommands(false)
      }
    } else {
      setShowCommands(false)
    }
  }

  const handleSlashKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>): boolean => {
    if (!showCommands || filteredCommands.length === 0) return false

    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setSelectedIndex((prev) => (prev + 1) % filteredCommands.length)
      return true
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault()
      setSelectedIndex((prev) => (prev - 1 + filteredCommands.length) % filteredCommands.length)
      return true
    }
    if (e.key === 'Enter' || e.key === 'Tab') {
      e.preventDefault()
      handleSelectCommand(filteredCommands[selectedIndex])
      return true
    }
    if (e.key === 'Escape') {
      e.preventDefault()
      setShowCommands(false)
      return true
    }

    return false
  }

  return {
    showCommands,
    setShowCommands,
    filteredCommands,
    selectedIndex,
    setSelectedIndex,
    handleSelectCommand,
    handleSlashFilter,
    handleSlashKeyDown,
  }
}
