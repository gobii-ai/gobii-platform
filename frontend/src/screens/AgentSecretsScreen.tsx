import { useCallback, useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, ExternalLink, Plus, ShieldCheck } from 'lucide-react'

import {
  fetchAgentSecrets,
  createAgentSecret,
  updateAgentSecret,
  deleteAgentSecret,
  promoteAgentSecret,
  type AgentSecretListResponse,
  type SecretDTO,
  type CreateSecretPayload,
  type UpdateSecretPayload,
} from '../api/secrets'
import { SecretTable } from '../components/secrets/SecretTable'
import { SecretFormModal } from '../components/secrets/SecretFormModal'
import { DeleteSecretDialog } from '../components/secrets/DeleteSecretDialog'
import { SettingsBanner } from '../components/agentSettings/SettingsBanner'
import { useModal } from '../hooks/useModal'
import { EmbeddedAgentShellBackButton } from '../components/agentChat/EmbeddedAgentShellBackButton'

type AgentSecretsScreenProps = {
  agentId: string
  agentName: string
  listUrl: string
  detailUrlTemplate: string
  promoteUrlTemplate: string
  agentDetailUrl: string
  globalSecretsUrl: string
  requestUrl: string
  variant?: 'standalone' | 'embedded'
  onBack?: () => void
}

const PLACEHOLDER_TOKEN = '00000000-0000-0000-0000-000000000000'

export function AgentSecretsScreen({
  agentId,
  agentName,
  listUrl,
  detailUrlTemplate,
  promoteUrlTemplate,
  agentDetailUrl,
  globalSecretsUrl,
  requestUrl,
  variant = 'standalone',
  onBack,
}: AgentSecretsScreenProps) {
  const queryClient = useQueryClient()
  const queryKey = useMemo(() => ['agent-secrets', agentId] as const, [agentId])
  const [modal, showModal] = useModal()
  const [banner, setBanner] = useState<string | null>(null)
  const [errorBanner, setErrorBanner] = useState<string | null>(null)

  const { data, isLoading, error } = useQuery<AgentSecretListResponse>({
    queryKey,
    queryFn: ({ signal }) => fetchAgentSecrets(listUrl, signal),
  })

  const agentSecrets = data?.agent_secrets ?? []
  const globalSecrets = data?.global_secrets ?? []
  const requestedSecrets = data?.requested_secrets ?? []
  const listError = error instanceof Error ? error.message : null

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

  const secretDetailUrl = (secretId: string) => detailUrlTemplate.replace(PLACEHOLDER_TOKEN, secretId)
  const secretPromoteUrl = (secretId: string) => promoteUrlTemplate.replace(PLACEHOLDER_TOKEN, secretId)
  const isEmbedded = variant === 'embedded'

  const handleCreate = useCallback(() => {
    showModal((onClose) => (
      <SecretFormModal
        showVisibilityToggle
        onClose={onClose}
        onSubmit={async (data) => {
          await createAgentSecret(listUrl, data as CreateSecretPayload)
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
            await updateAgentSecret(secretDetailUrl(secret.id), data as UpdateSecretPayload)
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
            await deleteAgentSecret(secretDetailUrl(secret.id))
            handleSuccess('Secret deleted.')
          }}
        />
      ))
    },
    [showModal, handleSuccess],
  )

  const handlePromote = useCallback(
    (secret: SecretDTO) => {
      if (!confirm(`Move "${secret.name}" to global secrets? It will be removed from this agent and shared across all your agents.`)) {
        return
      }
      promoteAgentSecret(secretPromoteUrl(secret.id))
        .then(() => handleSuccess(`"${secret.name}" promoted to global secret.`))
        .catch((err) => {
          setErrorBanner(err instanceof Error ? err.message : 'Failed to promote secret.')
        })
    },
    [handleSuccess],
  )

  return (
    <div className="space-y-6 pb-6">
      {modal}

      <SettingsBanner
        variant={isEmbedded ? 'embedded' : 'standalone'}
        leading={isEmbedded ? <EmbeddedAgentShellBackButton onClick={onBack} ariaLabel="Back to settings" /> : undefined}
        eyebrow={isEmbedded ? 'Agent secrets' : undefined}
        title={isEmbedded ? agentName : 'Agent Secrets'}
        subtitle={isEmbedded ? undefined : `Manage encrypted secrets for ${agentName}`}
        supportingContent={!isEmbedded ? (
          <a
            href={agentDetailUrl}
            className="group inline-flex items-center gap-2 text-sm text-blue-600 transition-colors hover:text-blue-800"
          >
            <ArrowLeft className="w-4 h-4 group-hover:-translate-x-0.5 transition-transform" />
            Back to Agent
          </a>
        ) : undefined}
        actions={(
          <button
            type="button"
            onClick={handleCreate}
            className={isEmbedded ? 'inline-flex w-max items-center gap-x-2 rounded-lg border border-blue-300/40 bg-blue-950/20 px-4 py-2 text-sm font-medium text-blue-100 transition-colors hover:border-blue-200 hover:bg-blue-900/30 focus:outline-none' : 'inline-flex w-max items-center gap-x-2 rounded-lg border border-transparent bg-blue-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2'}
          >
            <Plus className="w-4 h-4" />
            Add Secret
          </button>
        )}
      />

      {/* Security Notice */}
      <div className={isEmbedded ? 'overflow-hidden rounded-xl border border-blue-300/30 bg-blue-950/20 shadow-none' : 'bg-blue-50/80 backdrop-blur-sm border border-blue-200/60 shadow-xl rounded-xl overflow-hidden'}>
        <div className="p-4 sm:p-6">
          <div className="flex gap-x-4">
            <div className="flex-shrink-0">
              <ShieldCheck className={isEmbedded ? 'h-6 w-6 text-blue-300' : 'w-6 h-6 text-blue-600'} />
            </div>
            <div>
              <h3 className={isEmbedded ? 'mb-1 text-sm font-semibold text-blue-100' : 'text-sm font-semibold text-blue-800 mb-1'}>Secure Encryption</h3>
              <p className={isEmbedded ? 'text-sm text-blue-200/85' : 'text-sm text-blue-700'}>
                All secrets are encrypted with AES-256-GCM before storage. Credential secrets can be used via
                placeholders.
              </p>
            </div>
          </div>
        </div>
      </div>

      {/* Banners */}
      {banner && (
        <div className={isEmbedded ? 'rounded-lg border border-green-300/30 bg-green-950/20 px-4 py-3 text-sm text-green-100' : 'rounded-lg bg-green-50 border border-green-200 px-4 py-3 text-sm text-green-800'}>
          {banner}
        </div>
      )}
      {(errorBanner || listError) && (
        <div className={isEmbedded ? 'rounded-lg border border-red-300/30 bg-red-950/20 px-4 py-3 text-sm text-red-100' : 'rounded-lg bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-800'}>
          {errorBanner || listError}
        </div>
      )}

      {/* Loading */}
      {isLoading && (
        <div className="flex justify-center py-12">
          <div className={isEmbedded ? 'h-8 w-8 animate-spin rounded-full border-4 border-blue-300/30 border-t-blue-200' : 'h-8 w-8 animate-spin rounded-full border-4 border-blue-200 border-t-blue-600'} />
        </div>
      )}

      {/* Agent Secrets */}
      {!isLoading && (
        <>
          <SecretTable
            secrets={agentSecrets}
            embedded={isEmbedded}
            title="Agent Secrets"
            subtitle={`Secrets specific to ${agentName}`}
            emptyMessage="No agent-specific secrets configured."
            onEdit={handleEdit}
            onDelete={handleDelete}
            onPromote={handlePromote}
          />

          {/* Global Secrets (read-only) */}
          <div>
            <SecretTable
              secrets={globalSecrets}
              embedded={isEmbedded}
              readOnly
              title="Global Secrets"
              subtitle="Shared across all your agents. Agent-specific secrets override these on key conflict."
              emptyMessage="No global secrets configured."
            />
            <div className="mt-2 px-1">
              <a
                href={globalSecretsUrl}
                className={isEmbedded ? 'inline-flex items-center gap-1.5 text-sm text-blue-300 transition-colors hover:text-blue-200' : 'inline-flex items-center gap-1.5 text-sm text-blue-600 hover:text-blue-800 transition-colors'}
              >
                Manage global secrets
                <ExternalLink className="w-3.5 h-3.5" />
              </a>
            </div>
          </div>

          {/* Requested Secrets */}
          {requestedSecrets.length > 0 && (
            <div className={isEmbedded ? 'overflow-hidden rounded-xl border border-slate-200/70 bg-transparent shadow-none' : 'gobii-card-base'}>
              <div className="px-6 py-4 border-b border-gray-200/70 flex items-center justify-between">
                <div>
                  <h2 className={isEmbedded ? 'text-lg font-semibold text-slate-100' : 'text-lg font-semibold text-gray-800'}>Requested Secrets</h2>
                  <p className={isEmbedded ? 'mt-1 text-sm text-slate-400' : 'text-sm text-gray-500 mt-1'}>
                    {requestedSecrets.length} pending request{requestedSecrets.length !== 1 ? 's' : ''} awaiting values
                  </p>
                </div>
                <a
                  href={requestUrl}
                  className={isEmbedded ? 'inline-flex items-center gap-x-2 rounded-lg border border-indigo-300/40 bg-indigo-950/20 px-3 py-2 text-sm font-medium text-indigo-100 hover:border-indigo-200 hover:bg-indigo-900/30' : 'py-2 px-3 inline-flex items-center gap-x-2 text-sm font-medium rounded-lg border border-indigo-200 bg-indigo-50 text-indigo-700 hover:bg-indigo-100'}
                >
                  Provide Values
                </a>
              </div>
              <div className={isEmbedded ? 'divide-y divide-slate-200/70' : 'divide-y divide-gray-100'}>
                {requestedSecrets.map((s) => (
                  <div key={s.id} className="px-6 py-4 flex items-center justify-between">
                    <div>
                      <div className={isEmbedded ? 'text-sm font-medium text-slate-100' : 'text-sm font-medium text-gray-900'}>
                        {s.name}{' '}
                        <span className={isEmbedded ? 'text-xs text-slate-400' : 'text-xs text-gray-500'}>(Key: {s.key})</span>
                      </div>
                      {s.secret_type === 'env_var' ? (
                        <div className={isEmbedded ? 'text-xs text-slate-400' : 'text-xs text-gray-500'}>Type: Environment Variable</div>
                      ) : (
                        <div className={isEmbedded ? 'text-xs text-slate-400' : 'text-xs text-gray-500'}>
                          Type: Credential &bull; Domain: {s.domain_pattern}
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}
