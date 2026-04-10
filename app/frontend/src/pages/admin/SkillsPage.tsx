import { useState, useEffect, useMemo } from 'react'
import { Link } from 'react-router-dom'
import { Search, ChevronRight, List, LayoutGrid } from 'lucide-react'
import { Input } from '@/components/ui/input'
import { AdminLayout } from '@/components/layout/AdminLayout'
import { AdminCardSkeleton } from '@/components/ui/skeletons'
import { apiRequest, encodePathSegment } from '@/lib/bimemo-api'
import { cn } from '@/lib/utils'

interface SkillListItem {
  id: string
  name: string
  title: string
  description: string
  tool_sequence: string
  use_count: number
  enabled: boolean
  created_at: string
  last_status: string | null
  last_used_at: string | null
}

const FILTERS = ['All', 'Active', 'Disabled'] as const
type Filter = typeof FILTERS[number]

const SORT_OPTIONS = [
  { value: 'usage', label: 'By usage' },
  { value: 'name', label: 'By name' },
  { value: 'date', label: 'By date' },
  { value: 'lastrun', label: 'By last run' },
] as const
type SortKey = typeof SORT_OPTIONS[number]['value']

type ViewMode = 'list' | 'grid'

const TOOL_COLORS: Record<string, { bg: string; text: string }> = {
  web_search: { bg: 'bg-primary/10', text: 'text-primary' },
  http_fetch: { bg: 'bg-emerald-500/10', text: 'text-emerald-400' },
  file_read: { bg: 'bg-amber-500/10', text: 'text-amber-400' },
  file_write: { bg: 'bg-amber-500/10', text: 'text-amber-400' },
  file_search: { bg: 'bg-amber-500/10', text: 'text-amber-400' },
  email_send: { bg: 'bg-pink-500/10', text: 'text-pink-400' },
  memory_recall: { bg: 'bg-sky-500/10', text: 'text-sky-400' },
  reminder_create: { bg: 'bg-orange-500/10', text: 'text-orange-400' },
  reminder_list: { bg: 'bg-orange-500/10', text: 'text-orange-400' },
  system_status: { bg: 'bg-muted', text: 'text-muted-foreground' },
}

function getToolColor(tool: string) {
  return TOOL_COLORS[tool] || { bg: 'bg-muted', text: 'text-muted-foreground' }
}

function parseTools(toolSequence: string): string[] {
  if (!toolSequence) return []
  return [...new Set(toolSequence.split(',').map((t) => t.trim()).filter(Boolean))]
}

function statusDotColor(skill: SkillListItem): string {
  if (!skill.enabled) return 'bg-muted-foreground/40'
  if (!skill.last_status) return 'bg-muted-foreground/40'
  if (skill.last_status === 'success') return 'bg-emerald-500'
  if (skill.last_status === 'partial') return 'bg-amber-500'
  return 'bg-red-500'
}

function statusGlow(skill: SkillListItem): string {
  if (skill.enabled && skill.last_status === 'success') return 'shadow-[0_0_6px_rgba(34,197,94,0.4)]'
  return ''
}

function formatDate(iso: string | null): string {
  if (!iso) return ''
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
    })
  } catch { return '' }
}

/**
 * Admin page listing all saved skills with stats, filters, sorting, and dual view.
 */
export function SkillsPage() {
  const [skills, setSkills] = useState<SkillListItem[]>([])
  const [loading, setLoading] = useState(true)
  const [searchQuery, setSearchQuery] = useState('')
  const [filter, setFilter] = useState<Filter>('All')
  const [sort, setSort] = useState<SortKey>('usage')
  const [view, setView] = useState<ViewMode>('list')

  useEffect(() => {
    apiRequest<{ skills: SkillListItem[] }>('/admin/skills')
      .then((d) => setSkills(d.skills || []))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  const filtered = useMemo(() => {
    let result = skills

    // Filter
    if (filter === 'Active') result = result.filter((s) => s.enabled)
    if (filter === 'Disabled') result = result.filter((s) => !s.enabled)

    // Search
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase()
      result = result.filter((s) =>
        s.name.toLowerCase().includes(q) ||
        s.title.toLowerCase().includes(q) ||
        s.tool_sequence.toLowerCase().includes(q)
      )
    }

    // Sort
    result = [...result].sort((a, b) => {
      if (sort === 'name') return a.title.localeCompare(b.title)
      if (sort === 'usage') return b.use_count - a.use_count
      if (sort === 'date') return (b.created_at || '').localeCompare(a.created_at || '')
      if (sort === 'lastrun') return (b.last_used_at || '').localeCompare(a.last_used_at || '')
      return 0
    })

    return result
  }, [skills, searchQuery, filter, sort])

  const totalActive = skills.filter((s) => s.enabled).length
  const totalUses = skills.reduce((a, s) => a + s.use_count, 0)

  return (
    <AdminLayout title="Skills">
    <div className="flex-1 overflow-y-auto p-6 space-y-5">

      {/* Stats bar */}
      <div className="grid grid-cols-3 gap-3">
        {[
          { val: skills.length, label: 'Total skills', color: 'text-primary' },
          { val: totalActive, label: 'Active', color: 'text-emerald-400' },
          { val: totalUses, label: 'Total runs', color: 'text-amber-400' },
        ].map((s, i) => (
          <div key={i} className="rounded-xl border border-border bg-card px-4 py-3 flex items-baseline gap-2.5">
            <span className={cn('text-xl font-bold', s.color)}>{s.val}</span>
            <span className="text-xs text-muted-foreground">{s.label}</span>
          </div>
        ))}
      </div>

      {/* Search + Filter + Sort + View */}
      <div className="flex flex-wrap gap-2 items-center">
        {/* Search */}
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="Search by name, ID or tool..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="pl-9"
          />
        </div>

        {/* Filter chips */}
        <div className="flex gap-1">
          {FILTERS.map((f) => (
            <button
              key={f}
              type="button"
              onClick={() => setFilter(f)}
              className={cn(
                'rounded-md border px-3 py-1.5 text-xs transition-colors',
                filter === f
                  ? 'bg-primary/15 border-primary/30 text-primary'
                  : 'border-border text-muted-foreground hover:text-foreground'
              )}
            >
              {f}
            </button>
          ))}
        </div>

        {/* Sort */}
        <select
          value={sort}
          onChange={(e) => setSort(e.target.value as SortKey)}
          className="rounded-md border border-border bg-card px-2.5 py-1.5 text-xs text-muted-foreground"
        >
          {SORT_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>

        {/* View toggle */}
        <div className="flex border border-border rounded-md overflow-hidden">
          <button
            type="button"
            onClick={() => setView('list')}
            className={cn(
              'p-1.5 transition-colors',
              view === 'list' ? 'bg-muted text-foreground' : 'text-muted-foreground hover:text-foreground'
            )}
          >
            <List className="h-4 w-4" />
          </button>
          <button
            type="button"
            onClick={() => setView('grid')}
            className={cn(
              'p-1.5 transition-colors',
              view === 'grid' ? 'bg-muted text-foreground' : 'text-muted-foreground hover:text-foreground'
            )}
          >
            <LayoutGrid className="h-4 w-4" />
          </button>
        </div>
      </div>

      {/* Results count */}
      <div className="text-xs text-muted-foreground/60">
        {filtered.length} of {skills.length} skills
      </div>

      {/* Loading */}
      {loading && <AdminCardSkeleton count={4} />}

      {/* Empty */}
      {!loading && skills.length === 0 && (
        <p className="text-center text-muted-foreground py-12">
          No skills saved yet. Skills are created automatically when the agent solves complex tasks.
        </p>
      )}

      {/* No results */}
      {!loading && filtered.length === 0 && skills.length > 0 && (
        <p className="text-center text-muted-foreground py-8">No skills match your filters</p>
      )}

      {/* LIST VIEW */}
      {!loading && filtered.length > 0 && view === 'list' && (
        <div className="animate-in fade-in duration-300 space-y-1.5">
          {filtered.map((skill) => {
            const tools = parseTools(skill.tool_sequence)
            return (
              <Link
                key={skill.name}
                to={`/admin/skills/${encodePathSegment(skill.name)}`}
                className={cn(
                  'flex items-center gap-3 rounded-xl border border-border bg-card px-4 py-3.5',
                  'transition-colors hover:border-muted-foreground/30',
                  !skill.enabled && 'opacity-50',
                )}
              >
                {/* Status dot */}
                <span className={cn(
                  'h-2 w-2 rounded-full shrink-0',
                  statusDotColor(skill),
                  statusGlow(skill),
                )} />

                {/* Title + ID */}
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-medium truncate">{skill.title || skill.name}</div>
                  <div className="text-[10px] text-muted-foreground/50 font-mono mt-0.5">{skill.name}</div>
                </div>

                {/* Tool badges */}
                <div className="hidden sm:flex gap-1 shrink-0">
                  {tools.map((t) => {
                    const c = getToolColor(t)
                    return (
                      <span key={t} className={cn('rounded px-1.5 py-0.5 text-[10px] font-mono', c.bg, c.text)}>
                        {t}
                      </span>
                    )
                  })}
                </div>

                {/* Uses */}
                <div className="text-right shrink-0 min-w-[40px]">
                  <div className="text-sm font-semibold text-muted-foreground">{skill.use_count}</div>
                  <div className="text-[9px] text-muted-foreground/50">uses</div>
                </div>

                {/* Chevron */}
                <ChevronRight className="h-4 w-4 text-muted-foreground/30 shrink-0" />
              </Link>
            )
          })}
        </div>
      )}

      {/* GRID VIEW */}
      {!loading && filtered.length > 0 && view === 'grid' && (
        <div className="animate-in fade-in duration-300 grid grid-cols-1 sm:grid-cols-2 gap-3">
          {filtered.map((skill) => {
            const tools = parseTools(skill.tool_sequence)
            return (
              <Link
                key={skill.name}
                to={`/admin/skills/${encodePathSegment(skill.name)}`}
                className={cn(
                  'flex flex-col justify-between rounded-xl border border-border bg-card p-4 min-h-[140px]',
                  'transition-colors hover:border-muted-foreground/30',
                  !skill.enabled && 'opacity-50',
                )}
              >
                {/* Top */}
                <div>
                  <div className="flex items-center justify-between mb-2.5">
                    <span className={cn(
                      'h-2 w-2 rounded-full',
                      statusDotColor(skill),
                      statusGlow(skill),
                    )} />
                    <span className="text-[10px] text-muted-foreground/40 font-mono truncate max-w-[160px]">
                      {skill.name}
                    </span>
                  </div>
                  <div className="text-[13px] font-medium leading-snug line-clamp-2 mb-2.5">
                    {skill.title || skill.name}
                  </div>
                </div>

                {/* Bottom */}
                <div>
                  <div className="flex gap-1 flex-wrap mb-2.5">
                    {tools.map((t) => {
                      const c = getToolColor(t)
                      return (
                        <span key={t} className={cn('rounded px-1.5 py-0.5 text-[10px] font-mono', c.bg, c.text)}>
                          {t}
                        </span>
                      )
                    })}
                  </div>
                  <div className="flex items-center justify-between border-t border-border pt-2.5">
                    <div className="flex items-baseline gap-1">
                      <span className="text-base font-bold text-muted-foreground">{skill.use_count}</span>
                      <span className="text-[10px] text-muted-foreground/50">uses</span>
                    </div>
                    {skill.last_used_at && (
                      <span className="text-[10px] text-muted-foreground/40">{formatDate(skill.last_used_at)}</span>
                    )}
                  </div>
                </div>
              </Link>
            )
          })}
        </div>
      )}
    </div>
    </AdminLayout>
  )
}
