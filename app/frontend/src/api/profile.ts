import { apiRequest } from '@/lib/bimemo-api'

export interface UserProfile {
  first_name: string
  last_name: string
  display_name: string
  assistant_name: string
  timezone: string
  language: string
  custom_instructions: string
}

export async function getProfile(): Promise<UserProfile> {
  return apiRequest<UserProfile>('/admin/profile')
}

export async function saveProfile(profile: UserProfile): Promise<void> {
  await apiRequest('/admin/profile', {
    method: 'POST',
    body: JSON.stringify(profile),
  })
}

export async function uploadAvatar(type: 'user' | 'ai', file: File): Promise<{ url: string }> {
  const form = new FormData()
  form.append('file', file)
  return apiRequest<{ url: string }>(`/admin/avatars/${type}`, {
    method: 'POST',
    body: form,
  })
}

export async function deleteAvatar(type: 'user' | 'ai'): Promise<void> {
  await apiRequest(`/admin/avatars/${type}`, {
    method: 'DELETE',
  })
}
