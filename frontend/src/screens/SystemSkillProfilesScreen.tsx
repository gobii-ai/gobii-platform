import { useCallback, useEffect, useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, CircleAlert, Plus } from 'lucide-react'

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
  const [handledInitialSetupLink, setHandledInitialSetupLink] = useState(false)

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
  const bootstrapProfile = profiles.length === 1 && !profiles[0]?.complete ? profiles[0] : null
  const primaryActionLabel = bootstrapProfile ? 'Complete Setup' : profiles.length > 0 ? 'Add Another Profile' : 'Add Profile'

  const refresh = useCallback(() => {
    queryClient.invalidateQueries({ queryKey })
  }, [queryClient, queryKey])

  const formatSuccessMessage = useCallback((message: string, triggeredAgentCount?: number) => {
    if (!triggeredAgentCount) {
      return message
    }
    return `${message} Requeued ${triggeredAgentCount} agent${triggeredAgentCount === 1 ? '' : 's'}.`
  }, [])

  const handleSuccess = useCallback(
    (message: string, triggeredAgentCount?: number) => {
      setBanner(formatSuccessMessage(message, triggeredAgentCount))
      setErrorBanner(null)
      refresh()
    },
    [formatSuccessMessage, refresh],
  )

  const detailUrl = (profileId: string) => `${listUrl}${profileId}/`
  const defaultUrl = (profileId: string) => `${listUrl}${profileId}/default/`

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
            const response = await updateSystemSkillProfile(detailUrl(profile.id), payload as UpdateSystemSkillProfilePayload)
            handleSuccess('Profile updated.', response.triggered_agent_count)
          }}
        />
      ))
    },
    [definition, handleSuccess, showModal],
  )

  const handleCreate = useCallback(() => {
    if (bootstrapProfile) {
      handleEdit(bootstrapProfile)
      return
    }
    if (!definition) {
      return
    }
    showModal((onClose) => (
      <SystemSkillProfileFormModal
        definition={definition}
        onClose={onClose}
        onSubmit={async (payload) => {
          const response = await createSystemSkillProfile(listUrl, payload as CreateSystemSkillProfilePayload)
          handleSuccess('Profile created.', response.triggered_agent_count)
        }}
      />
    ))
  }, [bootstrapProfile, definition, handleEdit, handleSuccess, listUrl, showModal])

  useEffect(() => {
    if (handledInitialSetupLink || !definition) {
      return
    }

    const url = new URL(window.location.href)
    if (url.searchParams.get('setup') !== '1') {
      setHandledInitialSetupLink(true)
      return
    }

    const requestedProfileKey = url.searchParams.get('profile_key')?.trim() || null
    const requestedProfile = requestedProfileKey
      ? profiles.find((profile) => profile.profile_key === requestedProfileKey) ?? null
      : null

    if (requestedProfile) {
      handleEdit(requestedProfile)
    } else {
      handleCreate()
    }

    url.searchParams.delete('setup')
    url.searchParams.delete('profile_key')
    window.history.replaceState({}, '', `${url.pathname}${url.search ? url.search : ''}${url.hash}`)
    setHandledInitialSetupLink(true)
  }, [definition, handleCreate, handleEdit, handledInitialSetupLink, profiles])

  const handleDelete = useCallback(
    (profile: SystemSkillProfileDTO) => {
      showModal((onClose) => (
        <DeleteSystemSkillProfileDialog
          profileLabel={profile.label}
          onClose={onClose}
          onConfirm={async () => {
            const response = await deleteSystemSkillProfile(detailUrl(profile.id))
            handleSuccess('Profile deleted.', response.triggered_agent_count)
          }}
        />
      ))
    },
    [handleSuccess, showModal],
  )

  const handleSetDefault = useCallback(
    async (profile: SystemSkillProfileDTO) => {
      try {
        const response = await setDefaultSystemSkillProfile(defaultUrl(profile.id))
        handleSuccess(`Default profile updated to ${profile.label}.`, response.triggered_agent_count)
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
            {bootstrapProfile && (
              <p className="mt-2 text-sm font-medium text-blue-700">
                A default profile is ready. Add the credentials below to finish onboarding.
              </p>
            )}
            <p className="mt-2 text-sm text-slate-600">
              Need help getting these credentials? Ask your agent and it will walk you through setup.
            </p>
          </div>
          <button
            type="button"
            onClick={handleCreate}
            disabled={!definition}
            className="inline-flex w-max items-center gap-x-2 rounded-lg border border-transparent bg-blue-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:opacity-60"
          >
            <Plus className="h-4 w-4" />
            {primaryActionLabel}
          </button>
        </div>
      </div>

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
              Profiles are reusable owner-scoped credential sets. Agents can select them by profile key, but the default profile is used automatically.
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
                            Needs setup
                          </span>
                        )}
                      </div>
                      {!profile.complete && (
                        <p className="text-sm text-slate-500">
                          Add the required credentials to finish onboarding. Need help? Ask your agent.
                        </p>
                      )}
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
