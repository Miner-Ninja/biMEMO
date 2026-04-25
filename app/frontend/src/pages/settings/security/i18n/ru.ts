/**
 * Russian copy for the security settings page.
 *
 * Catalog labels and descriptions come from the backend per
 * `SecuritySetting`, so this file only carries page chrome
 * (titles, button labels, dialog copy) and category metadata
 * (icon name + display label) keyed by the canonical category id.
 */

import {
  Bot,
  FolderOpen,
  Globe,
  Handshake,
  Inbox,
  KeyRound,
  Layout,
  ScrollText,
  Send,
  Timer,
  Brain,
  type LucideIcon,
} from "lucide-react"

export interface CategoryMeta {
  label: string
  icon: LucideIcon
}

export const CATEGORY_META: Record<string, CategoryMeta> = {
  input_filter: { label: "Что вы пишете в biMEMO", icon: Inbox },
  output_filter: { label: "Что biMEMO пишет вам", icon: Send },
  filesystem: { label: "Доступ к файлам", icon: FolderOpen },
  network: { label: "Интернет и сеть", icon: Globe },
  trust: { label: "Подтверждения опасных действий", icon: Handshake },
  agent_limits: { label: "Лимиты на работу агента", icon: Timer },
  memory: { label: "Память и история", icon: Brain },
  llm: { label: "Поведение LLM", icon: Bot },
  auth: { label: "Аутентификация и сессии", icon: KeyRound },
  audit: { label: "Логи и аудит", icon: ScrollText },
  frontend: { label: "Внешний вид и интерфейс", icon: Layout },
}

export interface BuiltinMeta {
  label: string
  description: string
}

export const BUILTIN_META: Record<string, BuiltinMeta> = {
  paranoid: {
    label: "Параноид",
    description: "Максимальная защита: все блокировки включены.",
  },
  personal: {
    label: "Обычный",
    description: "Сбалансированный режим — текущее поведение biMEMO.",
  },
  developer: {
    label: "Программист",
    description: "Свобода для кода, инструментов и фетча документации.",
  },
}

export const PAGE_COPY = {
  title: "Безопасность",
  searchPlaceholder: "Найти настройку…",
  unsavedHeading: "Несохранённые изменения от профиля",
  saveAsNew: "Сохранить как новый профиль",
  discard: "Отменить изменения",
  overwrite: "Перезаписать профиль",
  activate: "Выбрать",
  active: "Активен",
  changedBadge: "Изменён",
  builtInLockHint: "Встроенный профиль нельзя изменять — клонируйте его.",
  alwaysOnSection: "Always On (нельзя отключить)",
  emptyCustomProfiles: "Пока нет своих профилей",
  cloneAction: "Клонировать",
  renameAction: "Переименовать",
  deleteAction: "Удалить",
  exportAction: "Экспортировать JSON",
  newProfileAction: "Создать свой",
  deleteConfirmTitle: "Удалить профиль?",
  deleteConfirmDescription:
    "Удаление кастомного профиля необратимо. Если профиль был активен, активным становится «Обычный».",
  cancel: "Отмена",
  confirm: "Подтвердить",
  whenOff: "Защита выключена.",
}

export const RISK_LABEL: Record<string, string> = {
  low: "Низкий риск",
  medium: "Средний риск",
  high: "Высокий риск",
  critical: "Always On",
}
