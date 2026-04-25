/**
 * One accordion section grouping settings of a single category.
 *
 * Header shows the category icon + Russian label + a count
 * badge. Children are individual SettingItem cards plus any
 * inline-warning slots they choose to render.
 */

import {
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion"
import { Badge } from "@/components/ui/badge"
import type { ReactNode } from "react"
import { CATEGORY_META } from "../i18n/ru"

interface CategorySectionProps {
  category: string
  count: number
  children: ReactNode
}

export function CategorySection({
  category,
  count,
  children,
}: CategorySectionProps) {
  const meta = CATEGORY_META[category]
  if (!meta) return null
  const Icon = meta.icon

  return (
    <AccordionItem value={category}>
      <AccordionTrigger className="hover:no-underline">
        <span className="flex items-center gap-3">
          <Icon className="size-5 text-muted-foreground" />
          <span className="text-base font-medium">{meta.label}</span>
          <Badge variant="secondary" className="ml-1 text-xs">
            {count}
          </Badge>
        </span>
      </AccordionTrigger>
      <AccordionContent>
        <div className="flex flex-col gap-2 pt-1">{children}</div>
      </AccordionContent>
    </AccordionItem>
  )
}
