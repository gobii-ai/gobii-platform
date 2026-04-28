import { useMemo } from 'react'

import { AgentFilesScreen } from '../../screens/AgentFilesScreen'
import type { AgentFilesPageData } from '../agentFiles/types'
import { EmbeddedAgentShellPanel } from './EmbeddedAgentShellPanel'

type EmbeddedAgentFilesPanelProps = {
  agentId: string
  agentName: string
  canManage: boolean
  onBack?: () => void
}

export function EmbeddedAgentFilesPanel({
  agentId,
  agentName,
  canManage,
  onBack,
}: EmbeddedAgentFilesPanelProps) {
  const initialData = useMemo<AgentFilesPageData>(() => ({
    csrfToken: '',
    agent: {
      id: agentId,
      name: agentName,
    },
    backLink: {
      url: `/console/agents/${agentId}/`,
      label: 'Back to Agent Settings',
    },
    permissions: {
      canManage,
    },
    urls: {
      files: `/console/api/agents/${agentId}/files/`,
      upload: `/console/api/agents/${agentId}/files/upload/`,
      delete: `/console/api/agents/${agentId}/files/delete/`,
      download: `/console/api/agents/${agentId}/files/download/`,
      createFolder: `/console/api/agents/${agentId}/files/folders/`,
      move: `/console/api/agents/${agentId}/files/move/`,
    },
  }), [agentId, agentName, canManage])

  return (
    <EmbeddedAgentShellPanel>
      <AgentFilesScreen
        initialData={initialData}
        variant="embedded"
        onBack={onBack}
      />
    </EmbeddedAgentShellPanel>
  )
}
