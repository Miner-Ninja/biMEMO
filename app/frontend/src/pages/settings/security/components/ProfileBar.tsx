/**
 * Horizontal profile selector + "create new" CTA.
 *
 * Cards live in a ScrollArea on mobile and in a flex row on
 * desktop. The "+ Create" entry is a separate plain Card with a
 * ghost button so the visual hierarchy of profiles stays
 * uniform.
 */

import { Plus } from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
import { ScrollArea, ScrollBar } from "@/components/ui/scroll-area"
import { Button } from "@/components/ui/button"
import type { SecurityProfile } from "@/api/security"
import { ProfileCard } from "./ProfileCard"
import { PAGE_COPY } from "../i18n/ru"

interface ProfileBarProps {
  profiles: SecurityProfile[]
  activeId: string | null
  changedCount: number
  onActivate: (id: string) => void
  onClone: (sourceId: string) => void
  onRename: (id: string) => void
  onDelete: (id: string) => void
  onCreate: () => void
}

export function ProfileBar({
  profiles,
  activeId,
  changedCount,
  onActivate,
  onClone,
  onRename,
  onDelete,
  onCreate,
}: ProfileBarProps) {
  return (
    <ScrollArea className="w-full">
      <div className="flex gap-3 pb-3">
        {profiles.map((profile) => (
          <div key={profile.id} className="min-w-[220px] max-w-[260px] flex-1">
            <ProfileCard
              profile={profile}
              isActive={profile.id === activeId}
              changedCount={profile.id === activeId ? changedCount : 0}
              onActivate={() => onActivate(profile.id)}
              onClone={() => onClone(profile.id)}
              onRename={() => onRename(profile.id)}
              onDelete={() => onDelete(profile.id)}
            />
          </div>
        ))}
        <div className="min-w-[180px] max-w-[200px] flex-1">
          <Card className="h-full border-dashed">
            <CardContent className="flex h-full flex-col items-center justify-center gap-2 p-4">
              <Button
                variant="ghost"
                onClick={onCreate}
                className="flex flex-col items-center gap-1 h-auto py-3"
              >
                <Plus className="size-5" />
                <span className="text-sm">{PAGE_COPY.newProfileAction}</span>
              </Button>
            </CardContent>
          </Card>
        </div>
      </div>
      <ScrollBar orientation="horizontal" />
    </ScrollArea>
  )
}
