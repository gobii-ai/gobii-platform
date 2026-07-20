import { useCallback, useMemo } from 'react'
import { ExternalLink, Plus } from 'lucide-react'

import { fetchAgentSecrets, createAgentSecret, updateAgentSecret, deleteAgentSecret, promoteAgentSecret, type AgentSecretListResponse, type SecretDTO } from '../api/secrets'
import { SecretTable } from '../components/secrets/SecretTable'
import { SecretSecurityNotice } from '../components/secrets/SecretSecurityNotice'
import { useSecretCrud } from '../components/secrets/useSecretCrud'
import { SettingsBanner } from '../components/agentSettings/SettingsBanner'
import { getSettingsSurfaceClassName } from '../components/common/SettingsSurface'
import { EmbeddedAgentShellBackButton } from '../components/agentChat/EmbeddedAgentShellBackButton'
import { SettingsActionButton } from '../components/agentSettings/SettingsControls'
import { InlineStatusBanner } from '../components/common/InlineStatusBanner'

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
  const queryKey = useMemo(() => ['agent-secrets', agentId] as const, [agentId])
  const secretDetailUrl = useCallback(
    (secretId: string) => detailUrlTemplate.replace(PLACEHOLDER_TOKEN, secretId),
    [detailUrlTemplate],
  )
  const secretPromoteUrl = useCallback(
    (secretId: string) => promoteUrlTemplate.replace(PLACEHOLDER_TOKEN, secretId),
    [promoteUrlTemplate],
  )
  const {
    data,
    isLoading,
    listError,
    modal,
    banner,
    errorBanner,
    setErrorBanner,
    handleCreate,
    handleEdit,
    handleDelete,
    handleSuccess,
  } = useSecretCrud<AgentSecretListResponse>({
    queryKey,
    listUrl,
    detailUrl: secretDetailUrl,
    fetchSecrets: fetchAgentSecrets,
    createSecret: createAgentSecret,
    updateSecret: updateAgentSecret,
    deleteSecret: deleteAgentSecret,
    showVisibilityToggle: true,
  })

  const agentSecrets = data?.agent_secrets ?? []
  const globalSecrets = data?.global_secrets ?? []
  const requestedSecrets = data?.requested_secrets ?? []

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
    [handleSuccess, secretPromoteUrl, setErrorBanner],
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
          <SettingsActionButton tone="primary" responsive onClick={handleCreate}>
            <Plus className="w-4 h-4" />
            Add Secret
          </SettingsActionButton>
        )}
      />

      <SecretSecurityNotice>
        All secrets are encrypted with AES-256-GCM before storage. Credential secrets can be used via
        placeholders.
      </SecretSecurityNotice>

      {/* Banners */}
      {banner && (
        <InlineStatusBanner variant="success" surface="embedded">{banner}</InlineStatusBanner>
      )}
      {(errorBanner || listError) && (
        <InlineStatusBanner variant="error" surface="embedded">{errorBanner || listError}</InlineStatusBanner>
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
                className="inline-flex items-center gap-1.5 text-sm text-blue-300 transition-colors hover:text-blue-200"
              >
                Manage global secrets
                <ExternalLink className="w-3.5 h-3.5" />
              </a>
            </div>
          </div>

          {/* Requested Secrets */}
          {requestedSecrets.length > 0 && (
            <div className={getSettingsSurfaceClassName({ variant: 'embedded', shadowClassName: 'shadow-none' })}>
              <div className="flex items-center justify-between border-b border-slate-200/15 px-6 py-4">
                <div>
                  <h2 className="text-lg font-semibold text-slate-100">Requested Secrets</h2>
                  <p className="mt-1 text-sm text-slate-400">
                    {requestedSecrets.length} pending request{requestedSecrets.length !== 1 ? 's' : ''} awaiting values
                  </p>
                </div>
                {onOpenRequests ? (
                  <SettingsActionButton
                    type="button"
                    onClick={onOpenRequests}
                    tone="primary"
                  >
                    Provide Values
                  </SettingsActionButton>
                ) : (
                  <SettingsActionButton
                    as="a"
                    href={requestUrl}
                    tone="primary"
                  >
                    Provide Values
                  </SettingsActionButton>
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
