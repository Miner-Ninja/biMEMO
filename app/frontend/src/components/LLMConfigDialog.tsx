import { useEffect, useState } from 'react'
import { X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { ModelCombobox } from '@/components/ModelCombobox'
import { testLLMConnection, getModelList } from '@/api/llm'
import type { LLMConfigEntry, ProviderType } from '@/api/llm'

const PROVIDER_LABELS: Record<ProviderType, string> = {
  openrouter: 'OpenRouter',
  ollama: 'Ollama (local)',
  custom: 'Custom LLM Provider',
}

const PROVIDER_SUBTITLES: Partial<Record<ProviderType, string>> = {
  custom: 'OpenAI-compatible API',
}

interface LLMConfigDialogProps {
  open: boolean
  onClose: () => void
  onSave: (data: {
    service_type: ProviderType
    api_key?: string
    model?: string
    dialog_recorder_model?: string
    base_url?: string
    dialog_recorder_base_url?: string
    num_ctx?: string
  }) => Promise<void>
  editConfig?: LLMConfigEntry | null
}

export function LLMConfigDialog({ open, onClose, onSave, editConfig }: LLMConfigDialogProps) {
  const isEdit = !!editConfig

  const [serviceType, setServiceType] = useState<ProviderType>('openrouter')
  const [apiKey, setApiKey] = useState('')
  const [model, setModel] = useState('')
  const [dialogRecorderModel, setDialogRecorderModel] = useState('')
  const [baseUrl, setBaseUrl] = useState('')
  const [dialogRecorderBaseUrl, setDialogRecorderBaseUrl] = useState('')

  const [models, setModels] = useState<string[]>([])
  const [loadingModels, setLoadingModels] = useState(false)
  const [testResult, setTestResult] = useState('')
  const [testing, setTesting] = useState(false)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (open) {
      if (editConfig) {
        setServiceType(editConfig.service_type)
        setModel(editConfig.model ?? '')
        setDialogRecorderModel(editConfig.dialog_recorder_model ?? '')
        setBaseUrl(editConfig.base_url ?? '')
        setDialogRecorderBaseUrl(editConfig.dialog_recorder_base_url ?? '')
        setApiKey('')
      } else {
        setServiceType('openrouter')
        setApiKey('')
        setModel('')
        setDialogRecorderModel('')
        setBaseUrl('')
        setDialogRecorderBaseUrl('')
      }
      setModels([])
      setTestResult('')
    }
  }, [open, editConfig])

  const showApiKey = serviceType === 'openrouter' || serviceType === 'custom'
  const showBaseUrl = serviceType === 'ollama' || serviceType === 'custom'
  const providerSubtitle = PROVIDER_SUBTITLES[serviceType]

  const loadModels = async () => {
    setLoadingModels(true)
    try {
      const result = await getModelList({
        service_type: serviceType,
        api_key: apiKey || undefined,
        base_url: baseUrl || undefined,
      })
      setModels(result.models)
    } catch {
      // silent
    } finally {
      setLoadingModels(false)
    }
  }

  const handleTest = async () => {
    setTesting(true)
    setTestResult('')
    try {
      const result = await testLLMConnection({
        service_type: serviceType,
        api_key: apiKey || undefined,
        model: model || undefined,
        base_url: baseUrl || undefined,
      })
      setTestResult(result.ok ? `Connected — ${result.provider ?? serviceType}` : `Failed: ${result.error ?? 'unknown'}`)
    } catch (err) {
      setTestResult(err instanceof Error ? err.message : 'Test failed')
    } finally {
      setTesting(false)
    }
  }

  const handleSubmit = async () => {
    setSaving(true)
    try {
      await onSave({
        service_type: serviceType,
        api_key: apiKey || undefined,
        model: model || undefined,
        dialog_recorder_model: dialogRecorderModel || undefined,
        base_url: baseUrl || undefined,
        dialog_recorder_base_url: dialogRecorderBaseUrl || undefined,
        num_ctx: editConfig?.num_ctx,
      })
    } finally {
      setSaving(false)
    }
  }

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="fixed inset-0 bg-black/50 backdrop-blur-sm" onClick={onClose} />
      <div className="relative z-50 w-full max-w-lg rounded-lg border border-border bg-background p-6 shadow-lg mx-4 max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold">
            {isEdit ? 'Edit LLM Configuration' : 'Add LLM Provider'}
          </h2>
          <button onClick={onClose} className="text-muted-foreground hover:text-foreground">
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="space-y-4">
          <div className="space-y-1">
            <label className="text-sm font-medium">Provider</label>
            <select
              value={serviceType}
              onChange={(e) => {
                setServiceType(e.target.value as ProviderType)
                setModels([])
              }}
              disabled={isEdit}
              className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
            >
              {(Object.keys(PROVIDER_LABELS) as ProviderType[]).map((key) => (
                <option key={key} value={key}>{PROVIDER_LABELS[key]}</option>
              ))}
            </select>
            {providerSubtitle && (
              <p className="text-xs text-muted-foreground">{providerSubtitle}</p>
            )}
          </div>

          {showApiKey && (
            <div className="space-y-1">
              <label className="text-sm font-medium">API Key</label>
              <Input
                type="password"
                placeholder={isEdit && editConfig?.has_api_key ? 'Configured — leave blank to keep current' : 'sk-…'}
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                onBlur={() => { if (apiKey) loadModels() }}
                autoComplete="off"
              />
              {isEdit && editConfig?.has_api_key && !apiKey && (
                <p className="text-xs text-muted-foreground">API key is saved. Enter a new value only to change it.</p>
              )}
            </div>
          )}

          {showBaseUrl && (
            <>
              <div className="space-y-1">
                <label className="text-sm font-medium">Base URL</label>
                <Input
                  placeholder={serviceType === 'ollama' ? 'http://ollama:11434' : 'https://api.example.com/v1'}
                  value={baseUrl}
                  onChange={(e) => setBaseUrl(e.target.value)}
                  onBlur={() => { if (serviceType === 'ollama' || baseUrl) loadModels() }}
                />
              </div>

              {(serviceType === 'ollama' || serviceType === 'custom') && (
                <div className="space-y-1">
                  <label className="text-sm font-medium">
                    Dialog Recorder Base URL <span className="text-muted-foreground font-normal">(optional)</span>
                  </label>
                  <Input
                    placeholder={serviceType === 'ollama' ? 'http://ollama-memory:11434' : 'http://vllm-dialog-recorder:8000'}
                    value={dialogRecorderBaseUrl}
                    onChange={(e) => setDialogRecorderBaseUrl(e.target.value)}
                  />
                </div>
              )}
            </>
          )}

          <div className="space-y-1">
            <div className="flex items-center justify-between">
              <label className="text-sm font-medium">Model</label>
              <button
                type="button"
                onClick={loadModels}
                disabled={loadingModels}
                className="text-xs text-muted-foreground hover:text-foreground disabled:opacity-50"
              >
                {loadingModels ? 'Loading…' : 'Load Models'}
              </button>
            </div>
            <ModelCombobox
              value={model}
              onChange={setModel}
              models={models}
              loading={loadingModels}
              placeholder={serviceType === 'openrouter' ? 'openai/gpt-4o' : serviceType === 'ollama' ? 'llama3.2' : 'model-name'}
            />
          </div>

          <div className="space-y-1">
            <label className="text-sm font-medium">
              Dialog Recorder Model <span className="text-muted-foreground font-normal">(optional)</span>
            </label>
            <ModelCombobox
              value={dialogRecorderModel}
              onChange={setDialogRecorderModel}
              models={models}
              loading={loadingModels}
              placeholder="Leave blank to use the same model"
            />
          </div>

          {testResult && (
            <p className={`text-sm ${testResult.startsWith('Connected') ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400'}`}>
              {testResult}
            </p>
          )}

          <div className="flex items-center gap-3 pt-2">
            <Button onClick={handleSubmit} disabled={saving}>
              {saving ? 'Saving…' : isEdit ? 'Update' : 'Create'}
            </Button>
            <Button variant="ghost" onClick={handleTest} disabled={testing}>
              {testing ? 'Testing…' : 'Test Connection'}
            </Button>
            <Button variant="ghost" onClick={onClose}>Cancel</Button>
          </div>
        </div>
      </div>
    </div>
  )
}
