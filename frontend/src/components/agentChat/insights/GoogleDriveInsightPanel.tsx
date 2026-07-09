import { useMutation } from '@tanstack/react-query'
import { FolderOpen, Loader2 } from 'lucide-react'

import { fetchNativeIntegrationPickerToken, recordNativeIntegrationAgentEvent, type NativeIntegrationProvider } from '../../../api/nativeIntegrations'
import { safeErrorMessage } from '../../../api/safeErrorMessage'
import { openGoogleDrivePicker, supportsNativeIntegrationPicker } from '../../mcp/NativeIntegrationShared'
import { NativeIntegrationConnectButton, NativeIntegrationInsightPanelFrame, useNativeIntegrationPanelState } from './NativeIntegrationInsightPanel'

const GOOGLE_DRIVE_PROVIDER_KEY = 'google_drive'

type GoogleDriveInsightPanelProps = {
  agentId?: string | null
  nativeIntegrationsUrl?: string | null
  onOpenApps?: () => void
}

const GOOGLE_DRIVE_FALLBACK_ICON = (
  <img src="/static/images/integrations/native/google_drive.svg" alt="" className="h-5 w-5 object-contain" />
)

export function GoogleDriveInsightPanel({ agentId = null, nativeIntegrationsUrl = null }: GoogleDriveInsightPanelProps) {
  const panel = useNativeIntegrationPanelState({
    agentId,
    nativeIntegrationsUrl,
    providerKey: GOOGLE_DRIVE_PROVIDER_KEY,
    providerDisplayName: 'Google',
  })

  const pickerMutation = useMutation({
    mutationFn: async (provider: NativeIntegrationProvider) => {
      const token = await fetchNativeIntegrationPickerToken(provider.pickerTokenUrl)
      const selectedFiles = await openGoogleDrivePicker(token)
      if (agentId && selectedFiles.length > 0) {
        await recordNativeIntegrationAgentEvent({
          agentEventUrl: provider.agentEventUrl,
          agentId,
          eventType: 'files_selected',
          files: selectedFiles,
        })
      }
      return { provider, selectedCount: selectedFiles.length }
    },
    onMutate: () => {
      panel.setPendingAction('picker')
      panel.setStatusMessage(null)
    },
    onSuccess: ({ selectedCount }) => {
      panel.setStatusMessage(selectedCount > 0 ? 'Selected files are now available to this agent.' : null)
    },
    onError: (error) => {
      panel.setStatusMessage(safeErrorMessage(error))
    },
    onSettled: () => panel.setPendingAction(null),
  })

  const busy = panel.connectPending || pickerMutation.isPending || panel.pendingAction !== null
  const pickerEnabled = Boolean(panel.provider?.connected && supportsNativeIntegrationPicker(panel.provider))

  return (
    <NativeIntegrationInsightPanelFrame
      ariaLabel="Google Drive"
      providerLabel="Google Drive"
      provider={panel.provider}
      connected={Boolean(panel.provider?.connected)}
      fallbackIcon={GOOGLE_DRIVE_FALLBACK_ICON}
      unavailableMessage={!nativeIntegrationsUrl ? 'Google Drive setup is unavailable in this workspace.' : null}
      loadingMessage={panel.isLoading ? 'Loading Google Drive...' : null}
      errorMessage={panel.errorMessage}
      notConfiguredMessage="Google Drive is not configured."
      title={panel.provider?.connected ? 'Google Drive connected' : 'Connect Google Drive'}
      text={panel.provider?.connected
        ? 'Choose Sheets files this agent can read or update.'
        : 'Connect Drive so this agent can work with selected Google Sheets.'}
      actions={panel.provider?.connected ? (
        <button
          type="button"
          className="google-drive-insight-panel__button google-drive-insight-panel__button--secondary"
          onClick={() => panel.provider && pickerMutation.mutate(panel.provider)}
          disabled={busy || !pickerEnabled}
        >
          {panel.pendingAction === 'picker' ? (
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          ) : (
            <FolderOpen className="h-4 w-4" aria-hidden="true" />
          )}
          Select files
        </button>
      ) : panel.provider ? (
        <NativeIntegrationConnectButton
          busy={busy}
          pendingAction={panel.pendingAction}
          onClick={() => panel.provider && panel.startConnect(panel.provider)}
        />
      ) : null}
      statusMessage={panel.statusMessage}
    />
  )
}
