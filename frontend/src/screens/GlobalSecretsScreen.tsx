import { useCallback, useMemo } from 'react'
import { Plus } from 'lucide-react'

import { fetchGlobalSecrets, createGlobalSecret, updateGlobalSecret, deleteGlobalSecret, type GlobalSecretListResponse } from '../api/secrets'
import { SecretTable } from '../components/secrets/SecretTable'
import { SecretSecurityNotice } from '../components/secrets/SecretSecurityNotice'
import { useSecretCrud } from '../components/secrets/useSecretCrud'
import { SettingsBanner } from '../components/agentSettings/SettingsBanner'

type GlobalSecretsScreenProps = {
  listUrl: string
  ownerScope?: string
}

export function GlobalSecretsScreen({ listUrl, ownerScope }: GlobalSecretsScreenProps) {
  const queryKey = useMemo(() => ['global-secrets', listUrl] as const, [listUrl])
  const detailUrl = useCallback((id: string) => `${listUrl}${id}/`, [listUrl])
  const {
    data,
    isLoading,
    listError,
    modal,
    banner,
    errorBanner,
    handleCreate,
    handleEdit,
    handleDelete,
  } = useSecretCrud<GlobalSecretListResponse>({
    queryKey,
    listUrl,
    detailUrl,
    fetchSecrets: fetchGlobalSecrets,
    createSecret: createGlobalSecret,
    updateSecret: updateGlobalSecret,
    deleteSecret: deleteGlobalSecret,
  })

  const secrets = data?.secrets ?? []
  const resolvedOwnerScope = ownerScope ?? data?.owner_scope
  const isOrganizationScope = resolvedOwnerScope === 'organization'
  const description = isOrganizationScope
    ? 'Manage encrypted secrets for this team.'
    : 'Manage encrypted secrets for your account.'
  const subtitle = isOrganizationScope
    ? 'Shared across all agents in this team'
    : 'Shared across all agents in your account'

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

      <SecretSecurityNotice>
        All secrets are encrypted with AES-256-GCM before storage. Global secrets are automatically
        available to all your agents.
      </SecretSecurityNotice>

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
