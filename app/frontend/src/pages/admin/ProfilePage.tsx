import { useCallback, useEffect, useRef, useState } from 'react'
import { Save, Upload, Trash2, User, Bot } from 'lucide-react'
import { AdminLayout } from '@/components/layout/AdminLayout'
import { FormSkeleton } from '@/components/ui/skeletons'
import { type UserProfile, getProfile, saveProfile, uploadAvatar, deleteAvatar } from '@/api/profile'

export function ProfilePage() {
  const [profile, setProfile] = useState<UserProfile>({
    first_name: '',
    last_name: '',
    display_name: '',
    assistant_name: '',
    timezone: '',
    language: '',
    custom_instructions: '',
  })
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')
  const [userAvatarUrl, setUserAvatarUrl] = useState<string | null>(null)
  const [aiAvatarUrl, setAiAvatarUrl] = useState<string | null>(null)
  const userFileRef = useRef<HTMLInputElement>(null)
  const aiFileRef = useRef<HTMLInputElement>(null)

  const checkAvatar = useCallback(async (type: 'user' | 'ai') => {
    try {
      const res = await fetch(`/avatars/${type}`, { credentials: 'include' })
      if (res.ok) {
        return `/avatars/${type}?t=${Date.now()}`
      }
    } catch { /* no avatar */ }
    return null
  }, [])

  useEffect(() => {
    Promise.all([
      getProfile(),
      checkAvatar('user'),
      checkAvatar('ai'),
    ])
      .then(([data, userUrl, aiUrl]) => {
        setProfile({
          first_name: data.first_name || '',
          last_name: data.last_name || '',
          display_name: data.display_name || '',
          assistant_name: data.assistant_name || '',
          timezone: data.timezone || '',
          language: data.language || '',
          custom_instructions: data.custom_instructions || '',
        })
        setUserAvatarUrl(userUrl)
        setAiAvatarUrl(aiUrl)
      })
      .catch((err) => setError(err instanceof Error ? err.message : 'Failed to load profile'))
      .finally(() => setLoading(false))
  }, [checkAvatar])

  const handleSave = async () => {
    setSaving(true)
    setError('')
    setSuccess('')
    try {
      await saveProfile(profile)
      setSuccess('Profile saved. Restart the application to apply changes to the system prompt.')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save profile')
    } finally {
      setSaving(false)
    }
  }

  const handleAvatarUpload = async (type: 'user' | 'ai', file: File) => {
    setError('')
    try {
      await uploadAvatar(type, file)
      const url = `/avatars/${type}?t=${Date.now()}`
      if (type === 'user') setUserAvatarUrl(url)
      else setAiAvatarUrl(url)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to upload avatar')
    }
  }

  const handleAvatarDelete = async (type: 'user' | 'ai') => {
    setError('')
    try {
      await deleteAvatar(type)
      if (type === 'user') setUserAvatarUrl(null)
      else setAiAvatarUrl(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete avatar')
    }
  }

  if (loading) {
    return (
      <AdminLayout title="User Profile">
        <div className="flex-1 overflow-y-auto p-6 max-w-2xl">
          <FormSkeleton fields={4} />
        </div>
      </AdminLayout>
    )
  }

  return (
    <AdminLayout title="User Profile">
    <div className="flex-1 overflow-y-auto p-6 space-y-6 max-w-2xl">
      <p className="text-muted-foreground">
        Configure how the AI assistant addresses you and responds
      </p>

      {error && (
        <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive">
          {error}
        </div>
      )}

      {success && (
        <div className="rounded-lg border border-green-500/50 bg-green-500/10 p-3 text-sm text-green-700 dark:text-green-400">
          {success}
        </div>
      )}

      <div className="grid grid-cols-2 gap-6">
        <div className="space-y-3">
          <span className="text-sm font-medium">Your Avatar</span>
          <div className="flex items-center gap-4">
            <div className="h-16 w-16 shrink-0 rounded-full border border-input bg-muted flex items-center justify-center overflow-hidden">
              {userAvatarUrl ? (
                <img src={userAvatarUrl} alt="User avatar" className="h-full w-full object-cover" />
              ) : (
                <User className="h-6 w-6 text-muted-foreground" />
              )}
            </div>
            <div className="flex flex-col gap-2">
              <button
                type="button"
                className="inline-flex items-center gap-1.5 rounded-md border border-input px-3 py-1.5 text-xs font-medium hover:bg-accent"
                onClick={() => userFileRef.current?.click()}
              >
                <Upload className="h-3.5 w-3.5" />
                Upload
              </button>
              {userAvatarUrl && (
                <button
                  type="button"
                  className="inline-flex items-center gap-1.5 rounded-md border border-input px-3 py-1.5 text-xs font-medium text-destructive hover:bg-destructive/10"
                  onClick={() => handleAvatarDelete('user')}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                  Remove
                </button>
              )}
            </div>
          </div>
          <input
            ref={userFileRef}
            type="file"
            className="hidden"
            accept=".jpg,.jpeg,.png,.webp,.gif"
            onChange={(e) => {
              const file = e.target.files?.[0]
              if (file) handleAvatarUpload('user', file)
              e.target.value = ''
            }}
          />
        </div>

        <div className="space-y-3">
          <span className="text-sm font-medium">AI Avatar</span>
          <div className="flex items-center gap-4">
            <div className="h-16 w-16 shrink-0 rounded-full border border-input bg-muted flex items-center justify-center overflow-hidden">
              {aiAvatarUrl ? (
                <img src={aiAvatarUrl} alt="AI avatar" className="h-full w-full object-cover" />
              ) : (
                <Bot className="h-6 w-6 text-muted-foreground" />
              )}
            </div>
            <div className="flex flex-col gap-2">
              <button
                type="button"
                className="inline-flex items-center gap-1.5 rounded-md border border-input px-3 py-1.5 text-xs font-medium hover:bg-accent"
                onClick={() => aiFileRef.current?.click()}
              >
                <Upload className="h-3.5 w-3.5" />
                Upload
              </button>
              {aiAvatarUrl && (
                <button
                  type="button"
                  className="inline-flex items-center gap-1.5 rounded-md border border-input px-3 py-1.5 text-xs font-medium text-destructive hover:bg-destructive/10"
                  onClick={() => handleAvatarDelete('ai')}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                  Remove
                </button>
              )}
            </div>
          </div>
          <input
            ref={aiFileRef}
            type="file"
            className="hidden"
            accept=".jpg,.jpeg,.png,.webp,.gif"
            onChange={(e) => {
              const file = e.target.files?.[0]
              if (file) handleAvatarUpload('ai', file)
              e.target.value = ''
            }}
          />
        </div>
      </div>

      <div className="space-y-4">
        <div className="space-y-2">
          <label htmlFor="assistant_name" className="text-sm font-medium">
            Assistant Name
          </label>
          <input
            id="assistant_name"
            type="text"
            className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
            placeholder="How should the assistant be called? (default: Assistant)"
            value={profile.assistant_name}
            onChange={(e) => setProfile({ ...profile, assistant_name: e.target.value })}
          />
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-2">
            <label htmlFor="first_name" className="text-sm font-medium">
              First Name
            </label>
            <input
              id="first_name"
              type="text"
              className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
              placeholder="First name"
              value={profile.first_name}
              onChange={(e) => setProfile({ ...profile, first_name: e.target.value })}
            />
          </div>
          <div className="space-y-2">
            <label htmlFor="last_name" className="text-sm font-medium">
              Last Name
            </label>
            <input
              id="last_name"
              type="text"
              className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
              placeholder="Last name"
              value={profile.last_name}
              onChange={(e) => setProfile({ ...profile, last_name: e.target.value })}
            />
          </div>
        </div>

        <div className="space-y-2">
          <label htmlFor="display_name" className="text-sm font-medium">
            Display Name
          </label>
          <input
            id="display_name"
            type="text"
            className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
            placeholder="How should the AI call you?"
            value={profile.display_name}
            onChange={(e) => setProfile({ ...profile, display_name: e.target.value })}
          />
        </div>

        <div className="space-y-2">
          <label htmlFor="timezone" className="text-sm font-medium">
            Timezone
          </label>
          <input
            id="timezone"
            type="text"
            className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
            placeholder="Europe/Moscow"
            value={profile.timezone}
            onChange={(e) => setProfile({ ...profile, timezone: e.target.value })}
          />
        </div>

        <div className="space-y-2">
          <label htmlFor="language" className="text-sm font-medium">
            Language
          </label>
          <input
            id="language"
            type="text"
            className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
            placeholder="ru"
            value={profile.language}
            onChange={(e) => setProfile({ ...profile, language: e.target.value })}
          />
        </div>

        <div className="space-y-2">
          <label htmlFor="custom_instructions" className="text-sm font-medium">
            Custom Instructions
          </label>
          <textarea
            id="custom_instructions"
            className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm min-h-[120px] resize-y"
            placeholder="Any specific instructions for the AI assistant..."
            maxLength={2000}
            value={profile.custom_instructions}
            onChange={(e) => setProfile({ ...profile, custom_instructions: e.target.value })}
          />
          <p className="text-xs text-muted-foreground">
            {profile.custom_instructions.length} / 2000
          </p>
        </div>
      </div>

      <button
        type="button"
        className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
        onClick={handleSave}
        disabled={saving}
      >
        <Save className="h-4 w-4" />
        {saving ? 'Saving...' : 'Save'}
      </button>
    </div>
    </AdminLayout>
  )
}
