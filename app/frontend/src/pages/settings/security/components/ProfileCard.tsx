/**
 * One profile card rendered in the profile bar.
 *
 * Shows the icon, label, and short description; surfaces
 * activation status via Badge; exposes a `...` dropdown with
 * Clone/Rename/Delete actions for non-built-in profiles.
 */

import {
  Code,
  Home,
  Lock,
  MoreVertical,
  Puzzle,
  type LucideIcon,
} from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import type { SecurityProfile } from "@/api/security"
import { BUILTIN_META, PAGE_COPY } from "../i18n/ru"

interface ProfileCardProps {
  profile: SecurityProfile
  isActive: boolean
  changedCount: number
  onActivate: () => void
  onClone: () => void
  onRename: () => void
  onDelete: () => void
}

const BUILTIN_ICON: Record<string, LucideIcon> = {
  paranoid: Lock,
  personal: Home,
  developer: Code,
}

export function ProfileCard({
  profile,
  isActive,
  changedCount,
  onActivate,
  onClone,
  onRename,
  onDelete,
}: ProfileCardProps) {
  const Icon = profile.is_builtin
    ? (BUILTIN_ICON[profile.id] ?? Puzzle)
    : Puzzle
  const meta = profile.is_builtin ? BUILTIN_META[profile.id] : null
  const label = meta?.label ?? profile.name
  const description = meta?.description ?? profile.description

  return (
    <Card
      className={isActive ? "border-primary" : undefined}
    >
      <CardHeader className="pb-2">
        <div className="flex items-start justify-between gap-2">
          <Icon className="size-5 text-muted-foreground" />
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                aria-label="Действия с профилем"
                className="-mr-1 size-8"
              >
                <MoreVertical />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuGroup>
                <DropdownMenuItem onSelect={onClone}>
                  {PAGE_COPY.cloneAction}
                </DropdownMenuItem>
                {!profile.is_builtin ? (
                  <>
                    <DropdownMenuItem onSelect={onRename}>
                      {PAGE_COPY.renameAction}
                    </DropdownMenuItem>
                    <DropdownMenuItem
                      onSelect={onDelete}
                      variant="destructive"
                    >
                      {PAGE_COPY.deleteAction}
                    </DropdownMenuItem>
                  </>
                ) : null}
              </DropdownMenuGroup>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
        <CardTitle className="text-base">{label}</CardTitle>
        {description ? (
          <CardDescription className="line-clamp-2">
            {description}
          </CardDescription>
        ) : null}
      </CardHeader>
      <CardContent className="pt-0 pb-2">
        {isActive ? (
          <Badge variant="default">
            {PAGE_COPY.active}
            {changedCount > 0 ? ` · ${PAGE_COPY.changedBadge} ${changedCount}` : ""}
          </Badge>
        ) : null}
      </CardContent>
      <CardFooter>
        <Button
          variant={isActive ? "secondary" : "outline"}
          size="sm"
          onClick={onActivate}
          disabled={isActive}
          className="w-full"
        >
          {isActive ? PAGE_COPY.active : PAGE_COPY.activate}
        </Button>
      </CardFooter>
    </Card>
  )
}
