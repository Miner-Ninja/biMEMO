import { z } from "zod";

import { apiRequest } from "@/lib/bimemo-api";

/**
 * User profile stored by the backend. Every field is optional: the
 * backend stores a free-form JSON dict and returns the empty object for
 * new users (no metadata yet).
 */
export const userProfileSchema = z.looseObject({
  first_name: z.string().optional(),
  last_name: z.string().optional(),
  display_name: z.string().optional(),
  assistant_name: z.string().optional(),
  timezone: z.string().optional(),
  language: z.string().optional(),
  custom_instructions: z.string().optional(),
});

export type UserProfile = z.infer<typeof userProfileSchema>;

/** Wire body of GET /admin/profile: the profile wrapped in a `profile` key. */
const getProfileResponseSchema = z.looseObject({
  profile: userProfileSchema,
});

/**
 * Load the user profile. The backend returns `{"profile": {...}}`, with an
 * empty object for new users; this unwraps the wrapper and returns the
 * profile itself so consumers see the stored fields (previously the wrapper
 * was returned as the profile, so every field read as undefined).
 */
export async function getProfile(): Promise<UserProfile> {
  const data = await apiRequest("/admin/profile", {}, getProfileResponseSchema);
  return data.profile;
}

export async function saveProfile(profile: UserProfile): Promise<void> {
  await apiRequest("/admin/profile", {
    method: "PUT",
    body: JSON.stringify(profile),
  });
}

/** Body of PUT /admin/avatars/{type}: the served URL of the stored avatar. */
const uploadAvatarResponseSchema = z.looseObject({
  url: z.string(),
});

export async function uploadAvatar(
  type: "user" | "ai",
  file: File
): Promise<{ url: string }> {
  const form = new FormData();
  form.append("file", file);
  return apiRequest(
    `/admin/avatars/${type}`,
    {
      method: "PUT",
      body: form,
    },
    uploadAvatarResponseSchema
  );
}

export async function deleteAvatar(type: "user" | "ai"): Promise<void> {
  await apiRequest(`/admin/avatars/${type}`, {
    method: "DELETE",
  });
}
