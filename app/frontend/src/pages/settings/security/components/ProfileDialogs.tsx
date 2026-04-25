/**
 * Modal dialogs for profile lifecycle: create, clone, rename,
 * and delete. Build/clone share a single create-form; rename
 * swaps name + description on an existing custom profile;
 * delete uses AlertDialog (destructive variant).
 */

import { type FormEvent, useEffect, useState } from "react"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Field,
  FieldDescription,
  FieldGroup,
  FieldLabel,
} from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import type { SecurityProfile } from "@/api/security"
import { PAGE_COPY } from "../i18n/ru"

const MAX_NAME_LENGTH = 80
const MAX_DESCRIPTION_LENGTH = 500

interface CreateOrCloneDialogProps {
  open: boolean
  source: SecurityProfile | null
  onOpenChange: (open: boolean) => void
  onSubmit: (name: string, description: string) => Promise<void>
}

export function CreateOrCloneDialog({
  open,
  source,
  onOpenChange,
  onSubmit,
}: CreateOrCloneDialogProps) {
  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState("")

  useEffect(() => {
    if (open) {
      setName(source ? `${source.name}-copy` : "")
      setDescription(source?.description ?? "")
      setError("")
    }
  }, [open, source])

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!name.trim()) return
    setSubmitting(true)
    try {
      await onSubmit(name.trim(), description.trim())
      onOpenChange(false)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Ошибка создания")
    } finally {
      setSubmitting(false)
    }
  }

  const title = source ? PAGE_COPY.cloneAction : PAGE_COPY.newProfileAction
  const subtitle = source
    ? `На основе профиля «${source.name}»`
    : "Будет создан на основе профиля «Обычный»"

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{subtitle}</DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit}>
          <FieldGroup>
            <Field>
              <FieldLabel htmlFor="profile-name">Имя профиля</FieldLabel>
              <Input
                id="profile-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                maxLength={MAX_NAME_LENGTH}
                required
                autoFocus
              />
              <FieldDescription>
                Уникальное имя — не повторяет встроенные.
              </FieldDescription>
            </Field>
            <Field>
              <FieldLabel htmlFor="profile-description">Описание</FieldLabel>
              <Textarea
                id="profile-description"
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                maxLength={MAX_DESCRIPTION_LENGTH}
                rows={3}
              />
            </Field>
          </FieldGroup>
          {error ? (
            <p className="pt-2 text-sm text-destructive">{error}</p>
          ) : null}
          <DialogFooter className="pt-4">
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
            >
              {PAGE_COPY.cancel}
            </Button>
            <Button
              type="submit"
              disabled={submitting || !name.trim()}
            >
              {PAGE_COPY.confirm}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

interface RenameDialogProps {
  open: boolean
  profile: SecurityProfile | null
  onOpenChange: (open: boolean) => void
  onSubmit: (name: string, description: string) => Promise<void>
}

export function RenameDialog({
  open,
  profile,
  onOpenChange,
  onSubmit,
}: RenameDialogProps) {
  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState("")

  useEffect(() => {
    if (open && profile) {
      setName(profile.name)
      setDescription(profile.description)
      setError("")
    }
  }, [open, profile])

  if (!profile) return null

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!name.trim()) return
    setSubmitting(true)
    try {
      await onSubmit(name.trim(), description.trim())
      onOpenChange(false)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Ошибка переименования")
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{PAGE_COPY.renameAction}</DialogTitle>
        </DialogHeader>
        <form onSubmit={handleSubmit}>
          <FieldGroup>
            <Field>
              <FieldLabel htmlFor="rename-name">Имя профиля</FieldLabel>
              <Input
                id="rename-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                maxLength={MAX_NAME_LENGTH}
                required
                autoFocus
              />
            </Field>
            <Field>
              <FieldLabel htmlFor="rename-description">Описание</FieldLabel>
              <Textarea
                id="rename-description"
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                maxLength={MAX_DESCRIPTION_LENGTH}
                rows={3}
              />
            </Field>
          </FieldGroup>
          {error ? (
            <p className="pt-2 text-sm text-destructive">{error}</p>
          ) : null}
          <DialogFooter className="pt-4">
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
            >
              {PAGE_COPY.cancel}
            </Button>
            <Button type="submit" disabled={submitting || !name.trim()}>
              {PAGE_COPY.confirm}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

interface DeleteDialogProps {
  open: boolean
  profile: SecurityProfile | null
  onOpenChange: (open: boolean) => void
  onConfirm: () => Promise<void>
}

export function DeleteDialog({
  open,
  profile,
  onOpenChange,
  onConfirm,
}: DeleteDialogProps) {
  const [submitting, setSubmitting] = useState(false)

  const handleConfirm = async () => {
    setSubmitting(true)
    try {
      await onConfirm()
      onOpenChange(false)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{PAGE_COPY.deleteConfirmTitle}</AlertDialogTitle>
          <AlertDialogDescription>
            {profile ? `«${profile.name}» — ${PAGE_COPY.deleteConfirmDescription}` : null}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>{PAGE_COPY.cancel}</AlertDialogCancel>
          <AlertDialogAction
            onClick={handleConfirm}
            disabled={submitting}
            className="bg-destructive text-destructive-foreground"
          >
            {PAGE_COPY.deleteAction}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
