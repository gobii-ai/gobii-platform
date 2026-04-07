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
import { useModal } from '../hooks/useModal'

type GlobalSecretsScreenProps = {
  listUrl: string
  ownerScope?: string
  ownerLabel?: string
}

const PLACEHOLDER_TOKEN = '00000000-0000-0000-0000-000000000000'

export function GlobalSecretsScreen({ listUrl, ownerScope, ownerLabel }: GlobalSecretsScreenProps) {
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
  const ownerText = ownerLabel || 'your workspace'

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

      {/* Header */}
      <div className="bg-white/80 backdrop-blur-sm shadow-xl rounded-xl overflow-hidden">
        <div className="px-6 py-4 border-b border-gray-200/70 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold text-gray-800">Secrets</h1>
            <p className="text-sm text-gray-500 mt-1">
              Manage encrypted secrets for {ownerText}. Global secrets are shared across all agents.
            </p>
          </div>
          <button
            type="button"
            onClick={handleCreate}
            className="py-2 px-4 inline-flex items-center gap-x-2 text-sm font-medium rounded-lg border border-transparent bg-blue-600 text-white hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 transition-colors w-max sm:self-center"
          >
            <Plus className="w-4 h-4" />
            Add Secret
          </button>
        </div>
      </div>

      {/* Security Notice */}
      <div className="bg-blue-50/80 backdrop-blur-sm border border-blue-200/60 shadow-xl rounded-xl overflow-hidden">
        <div className="p-4 sm:p-6">
          <div className="flex gap-x-4">
            <div className="flex-shrink-0">
              <ShieldCheck className="w-6 h-6 text-blue-600" />
            </div>
            <div>
              <h3 className="text-sm font-semibold text-blue-800 mb-1">Secure Encryption</h3>
              <p className="text-sm text-blue-700">
                All secrets are encrypted with AES-256-GCM before storage. Global secrets are automatically
                available to all your agents. Agent-specific secrets override global secrets when they share
                the same key.
              </p>
            </div>
          </div>
        </div>
      </div>

      {/* Banners */}
      {banner && (
        <div className="rounded-lg bg-green-50 border border-green-200 px-4 py-3 text-sm text-green-800">
          {banner}
        </div>
      )}
      {(errorBanner || listError) && (
        <div className="rounded-lg bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-800">
          {errorBanner || listError}
        </div>
      )}

      {/* Loading */}
      {isLoading && (
        <div className="flex justify-center py-12">
          <div className="h-8 w-8 animate-spin rounded-full border-4 border-blue-200 border-t-blue-600" />
        </div>
      )}

      {/* Table */}
      {!isLoading && (
        <SecretTable
          secrets={secrets}
          title="Global Secrets"
          subtitle={`Shared across all agents in ${ownerText}`}
          emptyMessage="No global secrets configured yet. Add your first secret to get started."
          onEdit={handleEdit}
          onDelete={handleDelete}
        />
      )}
    </div>
  )
}
