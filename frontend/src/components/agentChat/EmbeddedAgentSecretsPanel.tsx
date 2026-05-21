import { AgentSecretsScreen } from '../../screens/AgentSecretsScreen'
import { EmbeddedAgentShellPanel } from './EmbeddedAgentShellPanel'

type EmbeddedAgentSecretsPanelProps = {
  agentId: string
  agentName: string
  onBack?: () => void
}

const SECRET_PLACEHOLDER_ID = '00000000-0000-0000-0000-000000000000'

export function EmbeddedAgentSecretsPanel({
  agentId,
  agentName,
  onBack,
}: EmbeddedAgentSecretsPanelProps) {
  return (
    <EmbeddedAgentShellPanel>
      <AgentSecretsScreen
        agentId={agentId}
        agentName={agentName}
        listUrl={`/console/api/agents/${agentId}/secrets/`}
        detailUrlTemplate={`/console/api/agents/${agentId}/secrets/${SECRET_PLACEHOLDER_ID}/`}
        promoteUrlTemplate={`/console/api/agents/${agentId}/secrets/${SECRET_PLACEHOLDER_ID}/promote/`}
        globalSecretsUrl="/app/secrets"
        requestUrl={`/app/agents/${agentId}/secrets/request`}
        onBack={onBack}
      />
    </EmbeddedAgentShellPanel>
  )
}
