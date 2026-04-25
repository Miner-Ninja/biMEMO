/**
 * Settings -> Security top-level page.
 *
 * Wires the data hooks (catalog, profiles, active, dirty draft,
 * search) into the visual composition: header with search,
 * profile bar with cards, diff banner, accordion of editable
 * categories, and Always-On lock list at the bottom. All actions
 * surface toasts via sonner.
 */

import { useMemo, useState } from "react"
import { Search, ShieldCheck } from "lucide-react"
import { Toaster } from "@/components/ui/sonner"
import { Accordion } from "@/components/ui/accordion"
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from "@/components/ui/empty"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { Separator } from "@/components/ui/separator"
import { toast } from "sonner"
import type {
  SecurityProfile,
  SecuritySetting,
  SecuritySettingValue,
} from "@/api/security"
import { useSecurityCatalog } from "./hooks/useSecurityCatalog"
import { useSecurityProfiles } from "./hooks/useSecurityProfiles"
import { useActiveProfile } from "./hooks/useActiveProfile"
import { useDirtySettings } from "./hooks/useDirtySettings"
import { useSettingSearch } from "./hooks/useSettingSearch"
import { ProfileBar } from "./components/ProfileBar"
import { DiffBar } from "./components/DiffBar"
import { CategorySection } from "./components/CategorySection"
import { SettingItem } from "./components/SettingItem"
import { LockedSection } from "./components/LockedSection"
import {
  CreateOrCloneDialog,
  DeleteDialog,
  RenameDialog,
} from "./components/ProfileDialogs"
import { CATEGORY_META, PAGE_COPY } from "./i18n/ru"

export function SecurityPage() {
  const catalogResult = useSecurityCatalog()
  const profilesResult = useSecurityProfiles()
  const activeResult = useActiveProfile()
  const dirty = useDirtySettings(activeResult.profile)

  const [query, setQuery] = useState("")
  const [cloneSource, setCloneSource] = useState<SecurityProfile | null>(null)
  const [createOpen, setCreateOpen] = useState(false)
  const [renameTarget, setRenameTarget] = useState<SecurityProfile | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<SecurityProfile | null>(null)

  const editable = useMemo(
    () =>
      (catalogResult.catalog?.settings ?? []).filter((entry) => !entry.locked),
    [catalogResult.catalog],
  )
  const locked = useMemo(
    () =>
      (catalogResult.catalog?.settings ?? []).filter((entry) => entry.locked),
    [catalogResult.catalog],
  )
  const search = useSettingSearch(editable, query)

  const catalogByKey = useMemo(() => {
    const map: Record<string, SecuritySetting> = {}
    for (const entry of catalogResult.catalog?.settings ?? []) {
      map[entry.key] = entry
    }
    return map
  }, [catalogResult.catalog])

  const orderedCategories = useMemo(() => {
    const order = catalogResult.catalog?.categories ?? []
    return order.filter((category) => search.matchingCategories.has(category))
  }, [catalogResult.catalog, search.matchingCategories])

  const settingsByCategory = useMemo(() => {
    const map: Record<string, SecuritySetting[]> = {}
    for (const entry of search.filtered) {
      const bucket = map[entry.category] ?? []
      bucket.push(entry)
      map[entry.category] = bucket
    }
    return map
  }, [search.filtered])

  const handleActivate = async (id: string) => {
    if (id === activeResult.activeId) return
    if (dirty.isDirty) {
      toast.error("Сначала сохраните или отмените изменения активного профиля")
      return
    }
    try {
      await profilesResult.activate(id)
      await activeResult.refetch()
      toast.success(`Профиль активен: ${profileLabel(id, profilesResult.profiles)}`)
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Не удалось активировать")
    }
  }

  const handleSaveAsNew = () => {
    setCloneSource(activeResult.profile)
    setCreateOpen(true)
  }

  const handleClone = (sourceId: string) => {
    const source = profilesResult.profiles.find((entry) => entry.id === sourceId)
    if (!source) return
    setCloneSource(source)
    setCreateOpen(true)
  }

  const handleCreateNew = () => {
    setCloneSource(null)
    setCreateOpen(true)
  }

  const handleCreateSubmit = async (name: string, description: string) => {
    const carriesActiveDraft =
      dirty.isDirty && cloneSource?.id === activeResult.activeId
    const created = await profilesResult.create({
      name,
      description,
      base_profile_id: cloneSource?.id ?? null,
      settings: carriesActiveDraft ? dirty.draft : null,
    })
    if (carriesActiveDraft) {
      // The user saved their pending edits as a fresh profile —
      // activating it discards the now-stale diff against the
      // previous baseline and makes the new profile usable in
      // one continuous flow.
      await profilesResult.activate(created.id)
      await activeResult.refetch()
    }
    toast.success(`Создан профиль «${created.name}»`)
  }

  const handleRename = (id: string) => {
    const target = profilesResult.profiles.find((entry) => entry.id === id)
    if (!target || target.is_builtin) return
    setRenameTarget(target)
  }

  const handleRenameSubmit = async (name: string, description: string) => {
    if (!renameTarget) return
    await profilesResult.update(renameTarget.id, { name, description })
    toast.success(`Профиль переименован: «${name}»`)
  }

  const handleDelete = (id: string) => {
    const target = profilesResult.profiles.find((entry) => entry.id === id)
    if (!target || target.is_builtin) return
    setDeleteTarget(target)
  }

  const handleDeleteConfirm = async () => {
    if (!deleteTarget) return
    const result = await profilesResult.remove(deleteTarget.id)
    if (result.new_active_id) {
      await activeResult.refetch()
    }
    toast.success(`Удалён профиль «${deleteTarget.name}»`)
  }

  const handleOverwrite = async () => {
    if (!activeResult.profile || activeResult.profile.is_builtin) return
    try {
      await profilesResult.update(activeResult.profile.id, {
        settings: dirty.draft,
      })
      await activeResult.refetch()
      toast.success("Изменения сохранены")
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Не удалось сохранить")
    }
  }

  return (
    <div className="flex flex-col gap-4 p-6 max-w-5xl mx-auto">
      <header className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <h1 className="flex items-center gap-2 text-xl font-semibold">
          <ShieldCheck className="size-6 text-primary" />
          {PAGE_COPY.title}
        </h1>
        <div className="relative w-full sm:max-w-xs">
          <Search
            className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
            aria-hidden
          />
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={PAGE_COPY.searchPlaceholder}
            className="pl-9"
          />
        </div>
      </header>

      {profilesResult.loading ? (
        <ProfileBarSkeleton />
      ) : (
        <ProfileBar
          profiles={profilesResult.profiles}
          activeId={activeResult.activeId}
          changedCount={dirty.changes.length}
          onActivate={handleActivate}
          onClone={handleClone}
          onRename={handleRename}
          onDelete={handleDelete}
          onCreate={handleCreateNew}
        />
      )}

      <DiffBar
        changes={dirty.changes}
        activeProfileLabel={activeResult.profile?.name ?? ""}
        isBuiltinActive={activeResult.profile?.is_builtin ?? false}
        catalogByKey={catalogByKey}
        onSaveAsNew={handleSaveAsNew}
        onDiscard={dirty.reset}
        onOverwrite={handleOverwrite}
      />

      {catalogResult.loading ? (
        <CatalogSkeleton />
      ) : catalogResult.catalog ? (
        <Accordion multiple defaultValue={Object.keys(CATEGORY_META)}>
          {orderedCategories.map((category) => {
            const items = settingsByCategory[category] ?? []
            if (items.length === 0) return null
            return (
              <CategorySection
                key={category}
                category={category}
                count={items.length}
              >
                {items.map((entry) => (
                  <SettingItem
                    key={entry.key}
                    setting={entry}
                    value={
                      dirty.draft[entry.key] ?? entry.default_personal
                    }
                    baselineValue={activeResult.profile?.settings[entry.key]}
                    onChange={(next: SecuritySettingValue) =>
                      dirty.setValue(entry.key, next)
                    }
                  />
                ))}
              </CategorySection>
            )
          })}
        </Accordion>
      ) : (
        <Empty>
          <EmptyHeader>
            <EmptyTitle>Каталог недоступен</EmptyTitle>
            <EmptyDescription>{catalogResult.error}</EmptyDescription>
          </EmptyHeader>
        </Empty>
      )}

      <Separator />

      <LockedSection settings={locked} />

      <Toaster />

      <CreateOrCloneDialog
        open={createOpen}
        source={cloneSource}
        onOpenChange={setCreateOpen}
        onSubmit={handleCreateSubmit}
      />
      <RenameDialog
        open={renameTarget !== null}
        profile={renameTarget}
        onOpenChange={(open) => {
          if (!open) setRenameTarget(null)
        }}
        onSubmit={handleRenameSubmit}
      />
      <DeleteDialog
        open={deleteTarget !== null}
        profile={deleteTarget}
        onOpenChange={(open) => {
          if (!open) setDeleteTarget(null)
        }}
        onConfirm={handleDeleteConfirm}
      />
    </div>
  )
}

function profileLabel(id: string, profiles: SecurityProfile[]): string {
  return profiles.find((entry) => entry.id === id)?.name ?? id
}

function ProfileBarSkeleton() {
  return (
    <div className="flex gap-3 pb-3">
      {[0, 1, 2].map((index) => (
        <Skeleton key={index} className="h-32 w-56" />
      ))}
    </div>
  )
}

function CatalogSkeleton() {
  return (
    <div className="flex flex-col gap-2">
      {[0, 1, 2].map((index) => (
        <Skeleton key={index} className="h-12 w-full" />
      ))}
    </div>
  )
}
