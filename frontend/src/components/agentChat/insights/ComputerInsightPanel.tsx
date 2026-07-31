import { useQuery } from '@tanstack/react-query'
import { Monitor, Settings } from 'lucide-react'

import { fetchComputers, getComputerConnectionsUrl, type ComputerDevice } from '../../../api/computers'
import { NativeIntegrationInsightPanelFrame } from './NativeIntegrationInsightPanel'

function deviceStatus(device: ComputerDevice): string {
  if (device.update_required) return 'Update required'
  if (device.paused) return 'Paused'
  return device.online ? 'Online' : 'Offline'
}

function deviceSummary(device: ComputerDevice, includeName: boolean): string {
  const apps = device.apps
    .filter((app) => app.available && app.approval_state === 'approved')
    .map((app) => app.display_name)
  const prefix = includeName ? `${device.display_name}: ` : ''
  return `${prefix}${deviceStatus(device)} · ${apps.length ? apps.join(', ') : 'No desktop apps enabled'}`
}

export function ComputerInsightPanel({
  agentId = null,
  onManage,
}: {
  agentId?: string | null
  onManage?: () => void
}) {
  const computerConnectionsUrl = getComputerConnectionsUrl()
  const query = useQuery({
    queryKey: ['computer-connections', computerConnectionsUrl, agentId],
    queryFn: () => fetchComputers(computerConnectionsUrl as string, agentId),
    enabled: Boolean(computerConnectionsUrl && agentId),
    refetchInterval: 30_000,
  })
  const devices = query.data?.devices ?? []
  const onlineCount = devices.filter((device) => device.online && !device.paused && !device.update_required).length
  const title = devices.length === 1
    ? devices[0].display_name
    : devices.length > 1
      ? `${devices.length} computers assigned`
      : 'No computer connected'
  const text = devices.length === 1
    ? deviceSummary(devices[0], false)
    : devices.length > 1
      ? devices.map((device) => deviceSummary(device, true)).join(' • ')
      : 'Connect a Mac or Windows PC to let this Agent work with approved desktop apps.'

  return (
    <NativeIntegrationInsightPanelFrame
      ariaLabel="Computer"
      providerLabel="Computer"
      configured
      connected={onlineCount > 0}
      fallbackIcon={<Monitor className="h-5 w-5 text-violet-600" />}
      unavailableMessage={!computerConnectionsUrl || !agentId
        ? 'Computer connections are unavailable for this Agent.'
        : null}
      loadingMessage={query.isLoading ? 'Loading computers...' : null}
      errorMessage={query.error ? 'Computer status could not be loaded.' : null}
      notConfiguredMessage="Computer connections are unavailable."
      title={title}
      text={text}
      actions={onManage ? (
        <button
          type="button"
          onClick={onManage}
          className="google-drive-insight-panel__button google-drive-insight-panel__button--secondary"
        >
          <Settings className="h-4 w-4" aria-hidden="true" />
          Manage
        </button>
      ) : null}
    />
  )
}
