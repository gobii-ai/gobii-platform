import { useCallback, useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, CircleAlert, Plus, ShieldCheck } from 'lucide-react'

import {
  createSystemSkillProfile,
  deleteSystemSkillProfile,
  fetchSystemSkillProfiles,
  setDefaultSystemSkillProfile,
  updateSystemSkillProfile,
  type CreateSystemSkillProfilePayload,
  type SystemSkillProfileDTO,
  type UpdateSystemSkillProfilePayload,
} from '../api/systemSkillProfiles'
import { DeleteSystemSkillProfileDialog } from '../components/systemSkills/DeleteSystemSkillProfileDialog'
import { SystemSkillProfileFormModal } from '../components/systemSkills/SystemSkillProfileFormModal'
import { useModal } from '../hooks/useModal'


type SystemSkillProfilesScreenProps = {
  listUrl: string
  ownerScope?: string
  skillKey?: string
}


export function SystemSkillProfilesScreen({
  listUrl,
  ownerScope,
  skillKey,
}: SystemSkillProfilesScreenProps) {
  const queryClient = useQueryClient()
  const queryKey = useMemo(() => ['system-skill-profiles', listUrl] as const, [listUrl])
  const [modal, showModal] = useModal()
  const [banner, setBanner] = useState<string | null>(null)
  const [errorBanner, setErrorBanner] = useState<string | null>(null)

  const { data, isLoading, error } = useQuery({
    queryKey,
    queryFn: ({ signal }) => fetchSystemSkillProfiles(listUrl, signal),
  })

  const listError = error instanceof Error ? error.message : null
  const definition = data?.definition
  const profiles = data?.profiles ?? []
  const isOrganizationScope = (data?.owner_scope || ownerScope) === 'organization'
  const description = isOrganizationScope
    ? 'Manage reusable encrypted profiles for this organization.'
    : 'Manage reusable encrypted profiles for your account.'

  const refresh = useCallback(() => {
    queryClient.invalidateQueries({ queryKey })
  }, [queryClient, queryKey])

  const handleSuccess = useCallback(
    (message: string) => {
      setBanner(message)
      setErrorBanner(null)
      refresh()
    },
    [refresh],
  )

  const detailUrl = (profileId: string) => `${listUrl}${profileId}/`
  const defaultUrl = (profileId: string) => `${listUrl}${profileId}/default/`

  const handleCreate = useCallback(() => {
    if (!definition) {
      return
    }
    showModal((onClose) => (
      <SystemSkillProfileFormModal
        definition={definition}
        onClose={onClose}
        onSubmit={async (payload) => {
          await createSystemSkillProfile(listUrl, payload as CreateSystemSkillProfilePayload)
          handleSuccess('Profile created.')
        }}
      />
    ))
  }, [definition, handleSuccess, listUrl, showModal])

  const handleEdit = useCallback(
    (profile: SystemSkillProfileDTO) => {
      if (!definition) {
        return
      }
      showModal((onClose) => (
        <SystemSkillProfileFormModal
          definition={definition}
          editProfile={profile}
          onClose={onClose}
          onSubmit={async (payload) => {
            await updateSystemSkillProfile(detailUrl(profile.id), payload as UpdateSystemSkillProfilePayload)
            handleSuccess('Profile updated.')
          }}
        />
      ))
    },
    [definition, handleSuccess, showModal],
  )

  const handleDelete = useCallback(
    (profile: SystemSkillProfileDTO) => {
      showModal((onClose) => (
        <DeleteSystemSkillProfileDialog
          profileLabel={profile.label}
          onClose={onClose}
          onConfirm={async () => {
            await deleteSystemSkillProfile(detailUrl(profile.id))
            handleSuccess('Profile deleted.')
          }}
        />
      ))
    },
    [handleSuccess, showModal],
  )

  const handleSetDefault = useCallback(
    async (profile: SystemSkillProfileDTO) => {
      try {
        await setDefaultSystemSkillProfile(defaultUrl(profile.id))
        handleSuccess(`Default profile updated to ${profile.label}.`)
      } catch (err) {
        setErrorBanner(err instanceof Error ? err.message : 'Failed to set default profile.')
      }
    },
    [handleSuccess],
  )

  return (
    <div className="space-y-6 pb-6">
      {modal}

      <div className="overflow-hidden rounded-xl bg-white/80 shadow-xl backdrop-blur-sm">
        <div className="flex flex-col gap-4 px-6 py-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h1 className="text-2xl font-semibold text-slate-800">
              {definition?.name || skillKey || 'System Skill Profiles'}
            </h1>
            <p className="mt-1 text-sm text-slate-500">{description}</p>
            {definition?.search_summary && <p className="mt-2 text-sm text-slate-600">{definition.search_summary}</p>}
          </div>
          <button
            type="button"
            onClick={handleCreate}
            disabled={!definition}
            className="inline-flex w-max items-center gap-x-2 rounded-lg border border-transparent bg-blue-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:opacity-60"
          >
            <Plus className="h-4 w-4" />
            Add Profile
          </button>
        </div>
      </div>

      {definition && (
        <div className="overflow-hidden rounded-xl border border-blue-200/60 bg-blue-50/80 shadow-xl backdrop-blur-sm">
          <div className="p-4 sm:p-6">
            <div className="flex gap-x-4">
              <div className="shrink-0">
                <ShieldCheck className="h-6 w-6 text-blue-600" />
              </div>
              <div className="space-y-3">
                <div>
                  <h2 className="text-sm font-semibold text-blue-900">Setup</h2>
                  <p className="mt-1 text-sm text-blue-800">{definition.setup_instructions}</p>
                </div>
                <div>
                  <h3 className="text-xs font-semibold uppercase tracking-wide text-blue-700">Required Fields</h3>
                  <div className="mt-2 flex flex-wrap gap-2">
                    {definition.fields.map((field) => (
                      <span
                        key={field.key}
                        className={`rounded-full px-2.5 py-1 text-xs font-medium ${
                          field.required ? 'bg-blue-100 text-blue-800' : 'bg-white/80 text-blue-700'
                        }`}
                      >
                        {field.key}
                        {!field.required && field.default ? ` = ${field.default}` : ''}
                      </span>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {banner && <div className="rounded-lg border border-green-200 bg-green-50 px-4 py-3 text-sm text-green-800">{banner}</div>}
      {(errorBanner || listError) && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
          {errorBanner || listError}
        </div>
      )}

      {isLoading && (
        <div className="flex justify-center py-12">
          <div className="h-8 w-8 animate-spin rounded-full border-4 border-blue-200 border-t-blue-600" />
        </div>
      )}

      {!isLoading && (
        <div className="overflow-hidden rounded-xl bg-white/80 shadow-xl backdrop-blur-sm">
          <div className="px-6 py-4">
            <h2 className="text-lg font-semibold text-slate-800">Profiles</h2>
            <p className="mt-1 text-sm text-slate-500">
              Profiles are reusable owner-scoped credential sets. Agents can select them by profile key.
            </p>
          </div>

          {profiles.length === 0 ? (
            <div className="px-6 pb-6">
              <div className="rounded-xl border border-dashed border-slate-300 px-6 py-12 text-center">
                <p className="text-sm text-slate-600">No profiles configured yet.</p>
              </div>
            </div>
          ) : (
            <div className="divide-y divide-slate-100">
              {profiles.map((profile) => (
                <div key={profile.id} className="px-6 py-5">
                  <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
                    <div className="space-y-2">
                      <div className="flex flex-wrap items-center gap-2">
                        <h3 className="text-base font-semibold text-slate-800">{profile.label}</h3>
                        <span className="rounded-full bg-slate-100 px-2.5 py-1 text-xs font-medium text-slate-700">
                          {profile.profile_key}
                        </span>
                        {profile.is_default && (
                          <span className="rounded-full bg-blue-100 px-2.5 py-1 text-xs font-medium text-blue-800">
                            Default
                          </span>
                        )}
                        {profile.complete ? (
                          <span className="inline-flex items-center gap-1 rounded-full bg-emerald-100 px-2.5 py-1 text-xs font-medium text-emerald-800">
                            <CheckCircle2 className="h-3.5 w-3.5" />
                            Complete
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 rounded-full bg-amber-100 px-2.5 py-1 text-xs font-medium text-amber-800">
                            <CircleAlert className="h-3.5 w-3.5" />
                            Missing {profile.missing_required_keys.join(', ')}
                          </span>
                        )}
                      </div>
                      <div className="flex flex-wrap gap-2 text-xs text-slate-500">
                        {profile.present_keys.map((key) => (
                          <span key={key} className="rounded-full bg-slate-50 px-2 py-1 text-slate-600">
                            {key}
                          </span>
                        ))}
                      </div>
                    </div>

                    <div className="flex flex-wrap gap-2">
                      {!profile.is_default && (
                        <button
                          type="button"
                          onClick={() => void handleSetDefault(profile)}
                          className="rounded-md border border-blue-200 px-3 py-2 text-sm font-medium text-blue-700 transition hover:bg-blue-50"
                        >
                          Set Default
                        </button>
                      )}
                      <button
                        type="button"
                        onClick={() => handleEdit(profile)}
                        className="rounded-md border border-slate-200 px-3 py-2 text-sm font-medium text-slate-700 transition hover:bg-slate-50"
                      >
                        Edit
                      </button>
                      <button
                        type="button"
                        onClick={() => handleDelete(profile)}
                        className="rounded-md border border-red-200 px-3 py-2 text-sm font-medium text-red-700 transition hover:bg-red-50"
                      >
                        Delete
                      </button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
