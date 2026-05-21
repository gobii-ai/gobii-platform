import { useCallback, useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, ShieldCheck } from 'lucide-react'

import {
  fetchGlobalSecrets,
  createGlobalSecret,
  updateGlobalSecret,
  deleteGlobalSecret,
  type GlobalSecretListResponse,
  type SecretDTO,
  type CreateSecretPayload,
  type UpdateSecretPayload,
} from '../api/secrets'
import { SecretTable } from '../components/secrets/SecretTable'
import { SecretFormModal } from '../components/secrets/SecretFormModal'
import { DeleteSecretDialog } from '../components/secrets/DeleteSecretDialog'
import { SettingsBanner } from '../components/agentSettings/SettingsBanner'
import { embeddedSettingsSurfaceClassName, sharedSettingsGlassFrameClassName } from '../components/agentSettings/settingsSurfaceClasses'
import { useModal } from '../hooks/useModal'

type GlobalSecretsScreenProps = {
  listUrl: string
  ownerScope?: string
}

export function GlobalSecretsScreen({ listUrl, ownerScope }: GlobalSecretsScreenProps) {
  const queryClient = useQueryClient()
  const queryKey = useMemo(() => ['global-secrets', listUrl] as const, [listUrl])
  const [modal, showModal] = useModal()
  const [banner, setBanner] = useState<string | null>(null)
  const [errorBanner, setErrorBanner] = useState<string | null>(null)

  const { data, isLoading, error } = useQuery<GlobalSecretListResponse>({
    queryKey,
    queryFn: ({ signal }) => fetchGlobalSecrets(listUrl, signal),
  })

  const secrets = data?.secrets ?? []
  const listError = error instanceof Error ? error.message : null
  const resolvedOwnerScope = ownerScope ?? data?.owner_scope
  const isOrganizationScope = resolvedOwnerScope === 'organization'
  const description = isOrganizationScope
    ? 'Manage encrypted secrets for this organization.'
    : 'Manage encrypted secrets for your account.'
  const subtitle = isOrganizationScope
    ? 'Shared across all agents in this organization'
    : 'Shared across all agents in your account'

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

  // Derive detail URL from list URL (list = /console/api/secrets/, detail = /console/api/secrets/<id>/)
  const detailUrl = (id: string) => `${listUrl}${id}/`

  const handleCreate = useCallback(() => {
    showModal((onClose) => (
      <SecretFormModal
        onClose={onClose}
        onSubmit={async (data) => {
          await createGlobalSecret(listUrl, data as CreateSecretPayload)
          handleSuccess('Secret created.')
        }}
      />
    ))
  }, [listUrl, showModal, handleSuccess])

  const handleEdit = useCallback(
    (secret: SecretDTO) => {
      showModal((onClose) => (
        <SecretFormModal
          editSecret={secret}
          onClose={onClose}
          onSubmit={async (data) => {
            await updateGlobalSecret(detailUrl(secret.id), data as UpdateSecretPayload)
            handleSuccess('Secret updated.')
          }}
        />
      ))
    },
    [showModal, handleSuccess],
  )

  const handleDelete = useCallback(
    (secret: SecretDTO) => {
      showModal((onClose) => (
        <DeleteSecretDialog
          secretName={secret.name}
          onClose={onClose}
          onConfirm={async () => {
            await deleteGlobalSecret(detailUrl(secret.id))
            handleSuccess('Secret deleted.')
          }}
        />
      ))
    },
    [showModal, handleSuccess],
  )

  return (
    <div className="space-y-6 pb-6">
      {modal}

      <SettingsBanner
        variant="embedded"
        eyebrow="Workspace"
        title="Secrets"
        subtitle={description}
        actions={(
          <button
            type="button"
            onClick={handleCreate}
            className="inline-flex w-full items-center justify-center gap-x-2 rounded-lg border border-blue-300/40 bg-blue-950/20 px-4 py-2 text-sm font-medium text-blue-100 transition-colors hover:border-blue-200 hover:bg-blue-900/30 focus:outline-none sm:w-auto"
          >
            <Plus className="w-4 h-4" />
            Add Secret
          </button>
        )}
      />

      <div className={`${sharedSettingsGlassFrameClassName} ${embeddedSettingsSurfaceClassName} shadow-none`}>
        <div className="p-4 sm:p-6">
          <div className="flex gap-x-4">
            <div className="flex-shrink-0">
              <ShieldCheck className="h-6 w-6 text-slate-300" />
            </div>
            <div>
              <h3 className="mb-1 text-sm font-semibold text-slate-100">Secure Encryption</h3>
              <p className="text-sm text-slate-300">
                All secrets are encrypted with AES-256-GCM before storage. Global secrets are automatically
                available to all your agents.
              </p>
            </div>
          </div>
        </div>
      </div>

      {banner && (
        <div className="rounded-lg border border-green-300/30 bg-green-950/20 px-4 py-3 text-sm text-green-100">
          {banner}
        </div>
      )}
      {(errorBanner || listError) && (
        <div className="rounded-lg border border-red-300/30 bg-red-950/20 px-4 py-3 text-sm text-red-100">
          {errorBanner || listError}
        </div>
      )}

      {isLoading && (
        <div className="flex justify-center py-12">
          <div className="h-8 w-8 animate-spin rounded-full border-4 border-blue-300/30 border-t-blue-200" />
        </div>
      )}

      {!isLoading && (
        <SecretTable
          secrets={secrets}
          embedded
          title="Global Secrets"
          subtitle={subtitle}
          emptyMessage="No global secrets configured yet. Add your first secret to get started."
          onEdit={handleEdit}
          onDelete={handleDelete}
        />
      )}
    </div>
  )
}
