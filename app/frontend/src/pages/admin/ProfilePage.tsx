import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Save, Upload, Trash2, User, Bot } from "lucide-react";
import { AdminLayout } from "@/components/layout/AdminLayout";
import { TimezoneCombobox } from "@/components/TimezoneCombobox";
import { FormSkeleton } from "@/components/ui/skeletons";
import {
  type UserProfile,
  getProfile,
  saveProfile,
  uploadAvatar,
  deleteAvatar,
} from "@/api/profile";
import { queryClient } from "@/lib/query-client";
import { QUERY_KEYS, errorToDisplayText } from "@/hooks/use-server-state";

export function ProfilePage() {
  const { t } = useTranslation();
  const [profile, setProfile] = useState<UserProfile>({
    first_name: "",
    last_name: "",
    display_name: "",
    assistant_name: "",
    timezone: "",
    language: "",
    custom_instructions: "",
  });
  const [saving, setSaving] = useState(false);
  const [manualError, setManualError] = useState("");
  const [success, setSuccess] = useState("");
  const [userAvatarUrl, setUserAvatarUrl] = useState<string | null>(null);
  const [aiAvatarUrl, setAiAvatarUrl] = useState<string | null>(null);
  const userFileRef = useRef<HTMLInputElement>(null);
  const aiFileRef = useRef<HTMLInputElement>(null);

  const checkAvatarRaw = async (type: "user" | "ai"): Promise<string | null> => {
    try {
      const res = await fetch(`/avatars/${type}`, { credentials: "include" });
      if (res.ok) {
        return `/avatars/${type}`;
      }
    } catch {
      /* no avatar */
    }
    return null;
  };

  const {
    data: profileData,
    isInitialLoading: loading,
    error: queryError,
  } = useQuery({
    queryKey: QUERY_KEYS.profile,
    queryFn: async () => {
      const [profile, userAvatar, aiAvatar] = await Promise.all([
        getProfile(),
        checkAvatarRaw("user"),
        checkAvatarRaw("ai"),
      ]);
      return { profile, userAvatar, aiAvatar };
    },
  });

  // Seed the editable form state once, when the first data arrives.
  // Later refetches (after a save or an avatar change) must not wipe
  // in-progress field edits.
  const seededRef = useRef(false);
  useEffect(() => {
    if (!profileData || seededRef.current) return;
    seededRef.current = true;
    const p = profileData.profile;
    setProfile({
      first_name: p.first_name || "",
      last_name: p.last_name || "",
      display_name: p.display_name || "",
      assistant_name: p.assistant_name || "",
      timezone: p.timezone || "",
      language: p.language || "",
      custom_instructions: p.custom_instructions || "",
    });
    setUserAvatarUrl(profileData.userAvatar);
    setAiAvatarUrl(profileData.aiAvatar);
  }, [profileData]);

  const error = manualError || (queryError ? errorToDisplayText(queryError, t) : "");

  const saveMutation = useMutation({
    mutationFn: () => saveProfile(profile),
    onSuccess: () => {
      setSuccess(t("admin.profileSaved"));
      void queryClient.invalidateQueries({ queryKey: QUERY_KEYS.profile });
    },
    onError: (err) =>
      setManualError(err instanceof Error ? err.message : t("admin.saveProfileFailed")),
  });

  const handleSave = async () => {
    setSaving(true);
    setManualError("");
    setSuccess("");
    try {
      await saveMutation.mutateAsync();
    } catch {
      // display handled by the mutation's onError
    } finally {
      setSaving(false);
    }
  };

  const uploadAvatarMutation = useMutation({
    mutationFn: ({ type, file }: { type: "user" | "ai"; file: File }) =>
      uploadAvatar(type, file),
    onSuccess: (_result, variables) => {
      const url = `/avatars/${variables.type}?t=${Date.now()}`;
      if (variables.type === "user") setUserAvatarUrl(url);
      else setAiAvatarUrl(url);
      void queryClient.invalidateQueries({ queryKey: QUERY_KEYS.profile });
    },
    onError: (err) =>
      setManualError(
        err instanceof Error ? err.message : t("admin.uploadAvatarFailed")
      ),
  });

  const handleAvatarUpload = async (type: "user" | "ai", file: File) => {
    setManualError("");
    uploadAvatarMutation.mutate({ type, file });
  };

  const deleteAvatarMutation = useMutation({
    mutationFn: (type: "user" | "ai") => deleteAvatar(type),
    onSuccess: (_result, variables) => {
      if (variables === "user") setUserAvatarUrl(null);
      else setAiAvatarUrl(null);
      void queryClient.invalidateQueries({ queryKey: QUERY_KEYS.profile });
    },
    onError: (err) =>
      setManualError(
        err instanceof Error ? err.message : t("admin.deleteAvatarFailed")
      ),
  });

  const handleAvatarDelete = async (type: "user" | "ai") => {
    setManualError("");
    deleteAvatarMutation.mutate(type);
  };

  if (loading) {
    return (
      <AdminLayout title={t("admin.userProfile")}>
        <div className="flex-1 overflow-y-auto p-6 max-w-2xl">
          <FormSkeleton fields={4} />
        </div>
      </AdminLayout>
    );
  }

  return (
    <AdminLayout title={t("admin.userProfile")}>
      <div className="flex-1 overflow-y-auto p-6 space-y-6 max-w-2xl">
        <p className="text-muted-foreground">{t("admin.profileSubtitle")}</p>

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
            <span className="text-sm font-medium">{t("admin.yourAvatar")}</span>
            <div className="flex items-center gap-4">
              <div className="h-16 w-16 shrink-0 rounded-full border border-input bg-muted flex items-center justify-center overflow-hidden">
                {userAvatarUrl ? (
                  <img
                    src={userAvatarUrl}
                    alt={t("admin.userAvatarAlt")}
                    className="h-full w-full object-cover"
                  />
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
                  {t("admin.upload")}
                </button>
                {userAvatarUrl && (
                  <button
                    type="button"
                    className="inline-flex items-center gap-1.5 rounded-md border border-input px-3 py-1.5 text-xs font-medium text-destructive hover:bg-destructive/10"
                    onClick={() => handleAvatarDelete("user")}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                    {t("admin.remove")}
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
                const file = e.target.files?.[0];
                if (file) handleAvatarUpload("user", file);
                e.target.value = "";
              }}
            />
          </div>

          <div className="space-y-3">
            <span className="text-sm font-medium">{t("admin.aiAvatar")}</span>
            <div className="flex items-center gap-4">
              <div className="h-16 w-16 shrink-0 rounded-full border border-input bg-muted flex items-center justify-center overflow-hidden">
                {aiAvatarUrl ? (
                  <img
                    src={aiAvatarUrl}
                    alt={t("admin.aiAvatarAlt")}
                    className="h-full w-full object-cover"
                  />
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
                  {t("admin.upload")}
                </button>
                {aiAvatarUrl && (
                  <button
                    type="button"
                    className="inline-flex items-center gap-1.5 rounded-md border border-input px-3 py-1.5 text-xs font-medium text-destructive hover:bg-destructive/10"
                    onClick={() => handleAvatarDelete("ai")}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                    {t("admin.remove")}
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
                const file = e.target.files?.[0];
                if (file) handleAvatarUpload("ai", file);
                e.target.value = "";
              }}
            />
          </div>
        </div>

        <div className="space-y-4">
          <div className="space-y-2">
            <label htmlFor="assistant_name" className="text-sm font-medium">
              {t("admin.assistantName")}
            </label>
            <input
              id="assistant_name"
              type="text"
              className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
              placeholder={t("admin.assistantNamePlaceholder")}
              value={profile.assistant_name}
              onChange={(e) =>
                setProfile({ ...profile, assistant_name: e.target.value })
              }
            />
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <label htmlFor="first_name" className="text-sm font-medium">
                {t("admin.firstName")}
              </label>
              <input
                id="first_name"
                type="text"
                className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                placeholder={t("admin.firstNamePlaceholder")}
                value={profile.first_name}
                onChange={(e) => setProfile({ ...profile, first_name: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <label htmlFor="last_name" className="text-sm font-medium">
                {t("admin.lastName")}
              </label>
              <input
                id="last_name"
                type="text"
                className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                placeholder={t("admin.lastNamePlaceholder")}
                value={profile.last_name}
                onChange={(e) => setProfile({ ...profile, last_name: e.target.value })}
              />
            </div>
          </div>

          <div className="space-y-2">
            <label htmlFor="display_name" className="text-sm font-medium">
              {t("admin.displayName")}
            </label>
            <input
              id="display_name"
              type="text"
              className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
              placeholder={t("admin.displayNamePlaceholder")}
              value={profile.display_name}
              onChange={(e) => setProfile({ ...profile, display_name: e.target.value })}
            />
          </div>

          <div className="space-y-2">
            <label htmlFor="timezone" className="text-sm font-medium">
              {t("admin.timezone")}
            </label>
            <TimezoneCombobox
              value={profile.timezone || ""}
              onChange={(timezone) => setProfile({ ...profile, timezone })}
            />
          </div>

          <div className="space-y-2">
            <label htmlFor="language" className="text-sm font-medium">
              {t("admin.language")}
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
              {t("admin.customInstructions")}
            </label>
            <textarea
              id="custom_instructions"
              className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm min-h-[120px] resize-y"
              placeholder={t("admin.customInstructionsPlaceholder")}
              maxLength={2000}
              value={profile.custom_instructions}
              onChange={(e) =>
                setProfile({ ...profile, custom_instructions: e.target.value })
              }
            />
            <p className="text-xs text-muted-foreground">
              {(profile.custom_instructions || "").length} / 2000
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
          {saving ? t("admin.saving") : t("common.save")}
        </button>
      </div>
    </AdminLayout>
  );
}
