import { apiRequest } from "@/lib/bimemo-api"

export type SecuritySettingValue =
  | boolean
  | number
  | string
  | string[]

export type SecuritySettingType =
  | "toggle"
  | "slider_int"
  | "slider_float"
  | "enum"
  | "list_string"
  | "text"

export type SecurityRiskLevel = "low" | "medium" | "high" | "critical"

export interface SecurityEnumOption {
  value: string
  label: string
}

export interface SecurityConstraints {
  min: number | null
  max: number | null
  step: number | null
  unit: string | null
  options: SecurityEnumOption[] | null
  item_regex: string | null
  max_length: number | null
  placeholder: string | null
}

export interface SecuritySetting {
  key: string
  category: string
  label: string
  description: string
  type: SecuritySettingType
  risk_level: SecurityRiskLevel
  constraints: SecurityConstraints
  default_paranoid: SecuritySettingValue
  default_personal: SecuritySettingValue
  default_developer: SecuritySettingValue
  rationale: string | null
  example: string | null
  keywords: string[]
  locked: boolean
}

export interface SecurityCatalog {
  categories: string[]
  settings: SecuritySetting[]
}

export interface SecurityProfile {
  id: string
  name: string
  description: string
  is_builtin: boolean
  settings: Record<string, SecuritySettingValue>
  created_at: string
  updated_at: string
}

export interface ActiveProfileResponse {
  active_profile_id: string
  profile: SecurityProfile
}

export interface ActivateProfileResponse {
  active_profile_id: string
}

export interface DeleteProfileResponse {
  deleted_id: string
  new_active_id: string | null
}

export interface CreateProfileRequest {
  name: string
  description?: string
  base_profile_id?: string | null
  settings?: Record<string, SecuritySettingValue> | null
}

export interface UpdateProfileRequest {
  name?: string | null
  description?: string | null
  settings?: Record<string, SecuritySettingValue> | null
}

export async function getSecurityCatalog(): Promise<SecurityCatalog> {
  return apiRequest<SecurityCatalog>("/security/catalog")
}

export async function listSecurityProfiles(): Promise<SecurityProfile[]> {
  const response = await apiRequest<{ profiles: SecurityProfile[] }>(
    "/security/profiles",
  )
  return response.profiles
}

export async function getSecurityProfile(id: string): Promise<SecurityProfile> {
  const response = await apiRequest<{ profile: SecurityProfile }>(
    `/security/profiles/${encodeURIComponent(id)}`,
  )
  return response.profile
}

export async function getActiveSecurityProfile(): Promise<ActiveProfileResponse> {
  return apiRequest<ActiveProfileResponse>("/security/profiles/active")
}

export async function createSecurityProfile(
  body: CreateProfileRequest,
): Promise<SecurityProfile> {
  const response = await apiRequest<{ profile: SecurityProfile }>(
    "/security/profiles",
    {
      method: "POST",
      body: JSON.stringify(body),
    },
  )
  return response.profile
}

export async function updateSecurityProfile(
  id: string,
  body: UpdateProfileRequest,
): Promise<SecurityProfile> {
  const response = await apiRequest<{ profile: SecurityProfile }>(
    `/security/profiles/${encodeURIComponent(id)}`,
    {
      method: "PATCH",
      body: JSON.stringify(body),
    },
  )
  return response.profile
}

export async function deleteSecurityProfile(
  id: string,
): Promise<DeleteProfileResponse> {
  return apiRequest<DeleteProfileResponse>(
    `/security/profiles/${encodeURIComponent(id)}`,
    {
      method: "DELETE",
    },
  )
}

export async function activateSecurityProfile(
  id: string,
): Promise<ActivateProfileResponse> {
  return apiRequest<ActivateProfileResponse>(
    `/security/profiles/${encodeURIComponent(id)}/activate`,
    {
      method: "POST",
    },
  )
}
