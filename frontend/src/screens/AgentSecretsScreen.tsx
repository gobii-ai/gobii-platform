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
import { useModal } from '../hooks/useModal'

type AgentSecretsScreenProps = {
  agentId: string
  agentName: string
  listUrl: string
  detailUrlTemplate: string
  promoteUrlTemplate: string
  agentDetailUrl: string
  globalSecretsUrl: string
  requestUrl: string
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

      {/* Header */}
      <div className="bg-white/80 backdrop-blur-sm shadow-xl rounded-xl overflow-hidden">
        <div className="px-6 py-4 border-b border-gray-200/70 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold text-gray-800">Agent Secrets</h1>
            <p className="text-sm text-gray-500 mt-1">Manage encrypted secrets for {agentName}</p>
            <a
              href={agentDetailUrl}
              className="group flex items-center gap-2 text-sm text-blue-600 hover:text-blue-800 transition-colors mt-3"
            >
              <ArrowLeft className="w-4 h-4 group-hover:-translate-x-0.5 transition-transform" />
              Back to Agent
            </a>
          </div>

          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={handleCreate}
              className="py-2 px-4 inline-flex items-center gap-x-2 text-sm font-medium rounded-lg border border-transparent bg-blue-600 text-white hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 transition-colors w-max"
            >
              <Plus className="w-4 h-4" />
              Add Secret
            </button>
          </div>
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
                All secrets are encrypted with AES-256-GCM before storage. Credential secrets can be used via
                placeholders. Environment variable secrets are injected into sandbox execution.
                Agent-specific secrets override global secrets when they share the same key.
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

      {/* Agent Secrets */}
      {!isLoading && (
        <>
          <SecretTable
            secrets={agentSecrets}
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
              readOnly
              title="Global Secrets"
              subtitle="Shared across all your agents. Agent-specific secrets override these on key conflict."
              emptyMessage="No global secrets configured."
            />
            <div className="mt-2 px-1">
              <a
                href={globalSecretsUrl}
                className="inline-flex items-center gap-1.5 text-sm text-blue-600 hover:text-blue-800 transition-colors"
              >
                Manage global secrets
                <ExternalLink className="w-3.5 h-3.5" />
              </a>
            </div>
          </div>

          {/* Requested Secrets */}
          {requestedSecrets.length > 0 && (
            <div className="gobii-card-base">
              <div className="px-6 py-4 border-b border-gray-200/70 flex items-center justify-between">
                <div>
                  <h2 className="text-lg font-semibold text-gray-800">Requested Secrets</h2>
                  <p className="text-sm text-gray-500 mt-1">
                    {requestedSecrets.length} pending request{requestedSecrets.length !== 1 ? 's' : ''} awaiting values
                  </p>
                </div>
                <a
                  href={requestUrl}
                  className="py-2 px-3 inline-flex items-center gap-x-2 text-sm font-medium rounded-lg border border-indigo-200 bg-indigo-50 text-indigo-700 hover:bg-indigo-100"
                >
                  Provide Values
                </a>
              </div>
              <div className="divide-y divide-gray-100">
                {requestedSecrets.map((s) => (
                  <div key={s.id} className="px-6 py-4 flex items-center justify-between">
                    <div>
                      <div className="text-sm font-medium text-gray-900">
                        {s.name}{' '}
                        <span className="text-xs text-gray-500">(Key: {s.key})</span>
                      </div>
                      {s.secret_type === 'env_var' ? (
                        <div className="text-xs text-gray-500">Type: Environment Variable</div>
                      ) : (
                        <div className="text-xs text-gray-500">
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
