import { useCallback, useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ExternalLink, Plus, ShieldCheck } from 'lucide-react'

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
import { embeddedSettingsSurfaceClassName, sharedSettingsGlassFrameClassName } from '../components/agentSettings/settingsSurfaceClasses'
import { useModal } from '../hooks/useModal'
import { EmbeddedAgentShellBackButton } from '../components/agentChat/EmbeddedAgentShellBackButton'

type AgentSecretsScreenProps = {
  agentId: string
  agentName: string
  listUrl: string
  detailUrlTemplate: string
  promoteUrlTemplate: string
  globalSecretsUrl: string
  requestUrl: string
  onBack?: () => void
  onOpenRequests?: () => void
}

const PLACEHOLDER_TOKEN = '00000000-0000-0000-0000-000000000000'

export function AgentSecretsScreen({
  agentId,
  agentName,
  listUrl,
  detailUrlTemplate,
  promoteUrlTemplate,
  globalSecretsUrl,
  requestUrl,
  onBack,
  onOpenRequests,
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

      <SettingsBanner
        variant="embedded"
        leading={<EmbeddedAgentShellBackButton onClick={onBack} ariaLabel="Back to settings" />}
        eyebrow="Agent secrets"
        title={agentName}
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

      {/* Security Notice */}
      <div className={`${sharedSettingsGlassFrameClassName} ${embeddedSettingsSurfaceClassName} shadow-none`}>
        <div className="p-4 sm:p-6">
          <div className="flex gap-x-4">
            <div className="flex-shrink-0">
              <ShieldCheck className="h-6 w-6 text-slate-300" />
            </div>
            <div>
              <h3 className="mb-1 text-sm font-semibold text-slate-100">Secure Encryption</h3>
              <p className="text-sm text-slate-300">
                All secrets are encrypted with AES-256-GCM before storage. Credential secrets can be used via
                placeholders.
              </p>
            </div>
          </div>
        </div>
      </div>

      {/* Banners */}
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

      {/* Loading */}
      {isLoading && (
        <div className="flex justify-center py-12">
          <div className="h-8 w-8 animate-spin rounded-full border-4 border-blue-300/30 border-t-blue-200" />
        </div>
      )}

      {/* Agent Secrets */}
      {!isLoading && (
        <>
          <SecretTable
            secrets={agentSecrets}
            embedded
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
              embedded
              readOnly
              title="Global Secrets"
              subtitle="Shared across all your agents. Agent-specific secrets override these on key conflict."
              emptyMessage="No global secrets configured."
            />
            <div className="mt-2 px-1">
              <a
                href={globalSecretsUrl}
                className="inline-flex items-center gap-1.5 text-sm text-blue-300 transition-colors hover:text-blue-200"
              >
                Manage global secrets
                <ExternalLink className="w-3.5 h-3.5" />
              </a>
            </div>
          </div>

          {/* Requested Secrets */}
          {requestedSecrets.length > 0 && (
            <div className={`${sharedSettingsGlassFrameClassName} ${embeddedSettingsSurfaceClassName} shadow-none`}>
              <div className="flex items-center justify-between border-b border-slate-200/15 px-6 py-4">
                <div>
                  <h2 className="text-lg font-semibold text-slate-100">Requested Secrets</h2>
                  <p className="mt-1 text-sm text-slate-400">
                    {requestedSecrets.length} pending request{requestedSecrets.length !== 1 ? 's' : ''} awaiting values
                  </p>
                </div>
                {onOpenRequests ? (
                  <button
                    type="button"
                    onClick={onOpenRequests}
                    className="inline-flex items-center gap-x-2 rounded-lg border border-indigo-300/40 bg-indigo-950/20 px-3 py-2 text-sm font-medium text-indigo-100 hover:border-indigo-200 hover:bg-indigo-900/30"
                  >
                    Provide Values
                  </button>
                ) : (
                  <a
                    href={requestUrl}
                    className="inline-flex items-center gap-x-2 rounded-lg border border-indigo-300/40 bg-indigo-950/20 px-3 py-2 text-sm font-medium text-indigo-100 hover:border-indigo-200 hover:bg-indigo-900/30"
                  >
                    Provide Values
                  </a>
                )}
              </div>
              <div className="divide-y divide-slate-200/15">
                {requestedSecrets.map((s) => (
                  <div key={s.id} className="px-6 py-4 flex items-center justify-between">
                    <div>
                      <div className="text-sm font-medium text-slate-100">
                        {s.name}{' '}
                        <span className="text-xs text-slate-400">(Key: {s.key})</span>
                      </div>
                      {s.secret_type === 'env_var' ? (
                        <div className="text-xs text-slate-400">Type: Environment Variable</div>
                      ) : (
                        <div className="text-xs text-slate-400">
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
