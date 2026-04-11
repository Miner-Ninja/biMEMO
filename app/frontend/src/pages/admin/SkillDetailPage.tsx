import { useEffect, useRef, useState } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import { Pencil, Trash2, Check, X, Plus, Activity, ArrowLeft } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Switch } from '@/components/ui/switch'
import { AdminLayout } from '@/components/layout/AdminLayout'
import { AdminCardSkeleton } from '@/components/ui/skeletons'
import { apiRequest, encodePathSegment } from '@/lib/bimemo-api'
import { cn } from '@/lib/utils'

interface ProcedureStep {
  tool: string
  query: string
  note?: string
}

interface ErrorEntry {
  name: string
  fix: string
}

interface Skill {
  id: string
  name: string
  title: string
  description: string
  trigger: string
  procedure: ProcedureStep[]
  errors: ErrorEntry[]
  verification: string
  tool_sequence: string
  use_count: number
  enabled: boolean
  created_at: string
  updated_at: string
}

interface UsageLog {
  id: string
  question: string
  status: string
  duration_ms: number
  note: string
  created_at: string
}

const TABS = [
  { key: 'logic', label: 'Skill Logic' },
  { key: 'logs', label: 'History' },
]

const TOOL_OPTIONS = [
  'web_search', 'http_fetch', 'file_read', 'file_write', 'file_search',
  'memory_recall', 'email_send', 'reminder_create', 'reminder_list', 'system_status',
]

function formatDate(iso: string): string {
  if (!iso) return ''
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
    })
  } catch { return iso }
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

const STATUS_STYLES: Record<string, { bg: string; text: string; border: string; label: string }> = {
  success: { bg: 'bg-emerald-500/10', text: 'text-emerald-400', border: 'border-emerald-500/20', label: 'success' },
  partial: { bg: 'bg-amber-500/10', text: 'text-amber-400', border: 'border-amber-500/20', label: 'partial' },
  error: { bg: 'bg-red-500/10', text: 'text-red-400', border: 'border-red-500/20', label: 'error' },
}

const STATUS_DOT: Record<string, string> = {
  success: 'bg-emerald-500',
  partial: 'bg-amber-500',
  error: 'bg-red-500',
}

/**
 * Full-page skill editor with hero card and tabbed sections.
 */
export function SkillDetailPage() {
  const { name } = useParams<{ name: string }>()
  const navigate = useNavigate()

  const [skill, setSkill] = useState<Skill | null>(null)
  const [logs, setLogs] = useState<UsageLog[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState('logic')
  const [editing, setEditing] = useState(false)
  const [editData, setEditData] = useState<Partial<Skill>>({})
  const [showDelete, setShowDelete] = useState(false)
  const [saved, setSaved] = useState(false)
  const [panelMinHeight, setPanelMinHeight] = useState(200)
  const panelRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!name) return
    apiRequest<{ skill: Skill; logs: UsageLog[] }>(`/admin/skills/${encodePathSegment(name)}`)
      .then((d) => {
        setSkill(d.skill)
        setLogs(d.logs || [])
      })
      .catch((e) => setError(e.message || 'Failed to load skill'))
      .finally(() => setLoading(false))
  }, [name])

  useEffect(() => {
    const panel = panelRef.current
    if (!panel) return

    const resizeObserver = new ResizeObserver((entries) => {
      const entry = entries[0]
      if (!entry) return
      setPanelMinHeight((current) =>
        Math.max(current, Math.ceil(entry.contentRect.height)),
      )
    })

    resizeObserver.observe(panel)
    return () => resizeObserver.disconnect()
  }, [activeTab, editing])

  const startEdit = () => {
    if (!skill) return
    setEditData({
      title: skill.title,
      trigger: skill.trigger,
      procedure: [...skill.procedure],
      errors: [...skill.errors],
      verification: skill.verification,
    })
    setEditing(true)
  }

  const cancelEdit = () => {
    setEditing(false)
    setEditData({})
  }

  const saveEdit = async () => {
    if (!skill) return
    try {
      await apiRequest(`/admin/skills/${encodePathSegment(skill.name)}`, {
        method: 'PUT',
        body: JSON.stringify(editData),
      })
      setSkill({ ...skill, ...editData } as Skill)
      setEditing(false)
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed')
    }
  }

  const handleToggle = async () => {
    if (!skill) return
    const next = !skill.enabled
    try {
      await apiRequest(`/admin/skills/${encodePathSegment(skill.name)}`, {
        method: 'PUT',
        body: JSON.stringify({ enabled: next ? 1 : 0 }),
      })
      setSkill({ ...skill, enabled: next })
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to update skill status')
    }
  }

  const handleDelete = async () => {
    if (!skill) return
    try {
      await apiRequest(`/admin/skills/${encodePathSegment(skill.name)}`, { method: 'DELETE' })
      navigate('/admin/skills')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Delete failed')
    }
  }

  const updateStep = (i: number, key: keyof ProcedureStep, val: string) => {
    setEditData((d) => {
      const proc = [...(d.procedure || [])]
      proc[i] = { ...proc[i], [key]: val }
      return { ...d, procedure: proc }
    })
  }
  const addStep = () => {
    setEditData((d) => ({
      ...d,
      procedure: [...(d.procedure || []), { tool: 'web_search', query: '' }],
    }))
  }
  const removeStep = (i: number) => {
    setEditData((d) => ({
      ...d,
      procedure: (d.procedure || []).filter((_, idx) => idx !== i),
    }))
  }

  const updateError = (i: number, key: keyof ErrorEntry, val: string) => {
    setEditData((d) => {
      const errs = [...(d.errors || [])]
      errs[i] = { ...errs[i], [key]: val }
      return { ...d, errors: errs }
    })
  }
  const addError = () => {
    setEditData((d) => ({
      ...d,
      errors: [...(d.errors || []), { name: '', fix: '' }],
    }))
  }
  const removeError = (i: number) => {
    setEditData((d) => ({
      ...d,
      errors: (d.errors || []).filter((_, idx) => idx !== i),
    }))
  }

  if (loading) return <AdminLayout title="Skill"><div className="p-6"><AdminCardSkeleton count={2} /></div></AdminLayout>
  if (error && !skill) return <AdminLayout title="Skill"><div className="p-6 text-center text-destructive">{error}</div></AdminLayout>
  if (!skill) return <AdminLayout title="Skill"><div className="p-6 text-center text-muted-foreground">Skill not found</div></AdminLayout>

  const proc = editing ? (editData.procedure || []) : skill.procedure
  const errs = editing ? (editData.errors || []) : skill.errors

  return (
    <AdminLayout title={skill.title || 'Skill'}>
    <div className="flex-1 overflow-y-auto p-4 space-y-5 sm:p-6">
      {/* Top bar */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Link
          to="/admin/skills"
          className="inline-flex items-center gap-2 rounded-lg border border-border bg-card px-3 py-2 text-sm font-medium text-foreground transition-colors hover:bg-accent"
        >
          <ArrowLeft className="h-4 w-4" />
          Back to Skills
        </Link>
        <div className="flex items-center gap-2">
          {saved && (
            <span className="text-xs text-emerald-400 flex items-center gap-1 animate-in fade-in">
              <Check className="h-3.5 w-3.5" /> Saved
            </span>
          )}
          {editing ? (
            <>
              <Button size="sm" variant="outline" onClick={cancelEdit}>Cancel</Button>
              <Button size="sm" className="bg-primary" onClick={saveEdit}>Save</Button>
            </>
          ) : (
            <>
              <Button size="sm" variant="outline" onClick={startEdit} className="gap-1.5">
                <Pencil className="h-3.5 w-3.5" /> Edit
              </Button>
              <Button size="sm" variant="outline" onClick={() => setShowDelete(!showDelete)}
                className="gap-1.5 border-destructive/30 text-destructive hover:bg-destructive/10 hover:text-destructive">
                <Trash2 className="h-3.5 w-3.5" /> Delete
              </Button>
            </>
          )}
        </div>
      </div>

      {/* Edit mode banner */}
      {editing && (
        <div className="animate-in fade-in duration-200 rounded-lg border border-primary/20 bg-primary/5 px-4 py-2.5 text-sm text-primary flex items-center gap-2">
          <Pencil className="h-3.5 w-3.5" />
          Edit mode — modify fields and click Save
        </div>
      )}

      {/* Delete confirm */}
      {showDelete && (
        <div className="animate-in fade-in duration-200 rounded-lg border border-destructive/20 bg-destructive/5 px-4 py-3 flex items-center justify-between">
          <span className="text-sm text-destructive">Delete this skill permanently?</span>
          <div className="flex gap-2">
            <Button size="sm" variant="ghost" onClick={() => setShowDelete(false)}>Cancel</Button>
            <Button size="sm" variant="destructive" onClick={handleDelete}>Yes, delete</Button>
          </div>
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="animate-in fade-in duration-200 rounded-lg border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
          {error}
          <button type="button" className="ml-2 underline" onClick={() => setError(null)}>dismiss</button>
        </div>
      )}

      {/* Hero card */}
      <div className={cn(
        'rounded-xl border bg-card p-0 overflow-hidden transition-colors',
        editing ? 'border-primary/30' : 'border-border'
      )}>
        <div className="flex items-start gap-4 p-6 pb-5">
          {/* Skill icon */}
          <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-primary/20 to-primary/5 border border-primary/20">
            <Activity className="h-6 w-6 text-primary" />
          </div>

          <div className="flex-1 min-w-0">
            {editing ? (
              <Input
                value={editData.title || ''}
                onChange={(e) => setEditData((d) => ({ ...d, title: e.target.value }))}
                className="text-lg font-semibold mb-1"
                placeholder="Skill title"
              />
            ) : (
              <h2 className="text-lg font-semibold leading-tight">{skill.title}</h2>
            )}
            <span className="text-xs text-muted-foreground font-mono mt-1 block">{skill.name}</span>
          </div>

          <div className="flex items-center gap-2.5 shrink-0">
            <span className={cn(
              'text-xs font-medium',
              skill.enabled ? 'text-emerald-400' : 'text-muted-foreground'
            )}>
              {skill.enabled ? 'On' : 'Off'}
            </span>
            <Switch checked={skill.enabled} onCheckedChange={handleToggle} />
          </div>
        </div>

        {/* Metrics */}
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-5 border-t border-border">
          {[
            { val: String(skill.use_count), label: 'USES' },
            { val: `${skill.procedure.length} steps`, label: 'PROCEDURE' },
            { val: formatDate(skill.created_at), label: 'CREATED' },
            { val: formatDate(skill.updated_at), label: 'UPDATED' },
            { val: logs.length > 0 ? formatDate(logs[0].created_at) : '—', label: 'LAST USED' },
          ].map((m, i) => (
            <div key={i} className="border-b border-r border-border py-3.5 text-center last:border-r-0 sm:[&:nth-child(3n)]:border-r-0 md:[&:nth-child(3n)]:border-r md:last:border-r-0 md:[&:nth-child(-n+5)]:border-b-0">
              <div className="text-xs font-semibold sm:text-sm">{m.val}</div>
              <div className="text-[8px] text-muted-foreground/70 uppercase tracking-widest mt-1 sm:text-[9px]">{m.label}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Tabs + Panel connected */}
      <div>
        <div className="relative z-10 flex overflow-x-auto scrollbar-none -mb-px">
          {TABS.map((t) => (
            <button
              key={t.key}
              type="button"
              onClick={() => {
                const currentHeight = panelRef.current?.offsetHeight ?? 0
                setPanelMinHeight((current) => Math.max(current, currentHeight))
                setActiveTab(t.key)
              }}
              className={cn(
                'relative shrink-0 rounded-t-lg border px-3.5 py-3 transition-colors sm:px-5',
                activeTab === t.key
                  ? 'border-border border-b-transparent bg-card text-foreground'
                  : 'border-transparent text-muted-foreground hover:text-foreground'
              )}
            >
              <h4 className="text-base font-semibold tracking-tight">{t.label}</h4>
            </button>
          ))}
        </div>

        <div
          ref={panelRef}
          style={{ minHeight: `${panelMinHeight}px` }}
          className={cn(
            'animate-in fade-in duration-200 rounded-xl rounded-tl-none border bg-card p-7 transition-colors',
            editing ? 'border-primary/30' : 'border-border',
          )}
        >
        {activeTab === 'logic' && (
          <div className="space-y-8">
            <section>
              <h4 className="mb-4 text-base font-semibold tracking-tight text-foreground">1. When to use</h4>
              {editing ? (
                <Textarea
                  value={editData.trigger || ''}
                  onChange={(e) => setEditData((d) => ({ ...d, trigger: e.target.value }))}
                  rows={5}
                  placeholder="Describe when this skill should be triggered"
                />
              ) : (
                <p className="text-sm leading-relaxed text-foreground/85">{skill.trigger || 'No trigger description'}</p>
              )}
            </section>

            <section>
              <h4 className="mb-4 text-base font-semibold tracking-tight text-foreground">2. Action Flow</h4>
              <div className="space-y-3">
                {proc.map((step, i) => (
                  <div key={i} className="flex items-start gap-3 rounded-xl border border-border bg-muted/30 p-4">
                    <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full border-2 border-primary bg-primary/10 text-[11px] font-bold text-primary">
                      {i + 1}
                    </div>
                    <div className="min-w-0 flex-1">
                      {editing ? (
                        <>
                          <div className="mb-2 flex gap-2">
                            <select
                              value={step.tool}
                              onChange={(e) => updateStep(i, 'tool', e.target.value)}
                              className="rounded-md border border-border bg-background px-2.5 py-1.5 text-xs font-mono"
                            >
                              {TOOL_OPTIONS.map((t) => <option key={t} value={t}>{t}</option>)}
                            </select>
                            <button type="button" onClick={() => removeStep(i)} className="text-muted-foreground transition-colors hover:text-destructive">
                              <X className="h-4 w-4" />
                            </button>
                          </div>
                          <Input
                            value={step.query}
                            onChange={(e) => updateStep(i, 'query', e.target.value)}
                            placeholder="Query or parameters"
                            className="text-sm"
                          />
                        </>
                      ) : (
                        <>
                          <span className="inline-block rounded-md bg-primary/10 px-2 py-0.5 text-[11px] font-medium text-primary">
                            {step.tool}
                          </span>
                          <div className="mt-2 text-sm leading-relaxed text-foreground/85">{step.query}</div>
                          {step.note && <div className="mt-1.5 text-xs italic text-muted-foreground">{step.note}</div>}
                        </>
                      )}
                    </div>
                  </div>
                ))}
              </div>
              {editing && (
                <button type="button" onClick={addStep} className="mt-4 flex items-center gap-1.5 text-sm text-primary hover:underline">
                  <Plus className="h-3.5 w-3.5" /> Add step
                </button>
              )}
            </section>

            <section>
              <h4 className="mb-4 text-base font-semibold tracking-tight text-foreground">3. Issues &amp; Fixes</h4>
              <div className="space-y-3">
                {errs.map((err, i) => (
                  <div key={i} className="rounded-xl border border-border bg-muted/30 p-4">
                    {editing ? (
                      <div className="space-y-3">
                        <div className="flex items-center justify-between">
                          <label className="text-[10px] font-medium uppercase tracking-[0.06em] text-amber-400">Error</label>
                          <button type="button" onClick={() => removeError(i)} className="flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-destructive">
                            <X className="h-3 w-3" /> Remove
                          </button>
                        </div>
                        <Input
                          value={err.name}
                          onChange={(e) => updateError(i, 'name', e.target.value)}
                          placeholder="Error name"
                          className="font-medium"
                        />
                        <label className="block text-[10px] font-medium uppercase tracking-[0.06em] text-emerald-400">Solution</label>
                        <Textarea
                          value={err.fix}
                          onChange={(e) => updateError(i, 'fix', e.target.value)}
                          rows={2}
                          placeholder="How to handle this error"
                        />
                      </div>
                    ) : (
                      <div className="grid grid-cols-[140px_1fr] items-baseline gap-4">
                        <div className="flex items-center gap-2 text-xs font-semibold text-amber-400">
                          <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-amber-400" />
                          {err.name}
                        </div>
                        <div className="text-sm leading-relaxed text-muted-foreground">{err.fix}</div>
                      </div>
                    )}
                  </div>
                ))}
              </div>
              {editing && (
                <button type="button" onClick={addError} className="mt-4 flex items-center gap-1.5 text-sm text-primary hover:underline">
                  <Plus className="h-3.5 w-3.5" /> Add error
                </button>
              )}
            </section>

            <section>
              <h4 className="mb-4 text-base font-semibold tracking-tight text-foreground">4. Result verification</h4>
              {editing ? (
                <Textarea
                  value={editData.verification || ''}
                  onChange={(e) => setEditData((d) => ({ ...d, verification: e.target.value }))}
                  rows={5}
                  placeholder="How to confirm the result is correct"
                />
              ) : (
                <p className="text-sm leading-relaxed text-foreground/85">{skill.verification || 'No verification criteria'}</p>
              )}
            </section>
          </div>
        )}

        {activeTab === 'logs' && (
          <div>
            <div className="flex items-center justify-between mb-4">
              <div className="text-[10px] uppercase tracking-[0.08em] text-muted-foreground font-medium">Usage history</div>
              <span className="text-xs text-muted-foreground">{logs.length} entries</span>
            </div>
            {logs.length === 0 ? (
              <p className="text-sm text-muted-foreground py-8 text-center">No usage recorded yet</p>
            ) : (
              <div className="space-y-3">
                {logs.map((log) => {
                  const style = STATUS_STYLES[log.status] || STATUS_STYLES.success
                  const dot = STATUS_DOT[log.status] || STATUS_DOT.success
                  return (
                    <div key={log.id} className="rounded-xl border border-border bg-muted/30 p-4">
                      <div className="flex items-center justify-between mb-2.5">
                        <div className="flex items-center gap-2.5">
                          <span className={cn('h-2.5 w-2.5 rounded-full shrink-0', dot)} />
                          <span className="text-xs text-muted-foreground font-mono">{formatDate(log.created_at)}</span>
                          <span className={cn(
                            'text-[10px] px-2 py-0.5 rounded-full font-medium border',
                            style.bg, style.text, style.border,
                          )}>
                            {style.label}
                          </span>
                        </div>
                        <span className="text-xs text-muted-foreground">{formatDuration(log.duration_ms)}</span>
                      </div>
                      <div className="text-sm text-foreground/85 leading-relaxed">{log.question}</div>
                      {log.note && (
                        <div className={cn(
                          'text-xs mt-2.5 pt-2.5 border-t border-border flex items-start gap-1.5',
                          log.status === 'error' ? 'text-red-400' : 'text-amber-400',
                        )}>
                          <span className="shrink-0 mt-0.5">→</span>
                          {log.note}
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        )}
        </div>
      </div>
    </div>
    </AdminLayout>
  )
}
