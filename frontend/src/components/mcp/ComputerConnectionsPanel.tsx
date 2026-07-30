import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Apple,
  Download,
  Monitor,
  Pause,
  Play,
  Settings2,
  Trash2,
} from 'lucide-react'

import {
  approveComputerPairing,
  fetchComputerPairing,
  fetchComputers,
  revokeComputer,
  revokeComputerAssignment,
  updateComputer,
  type ComputerDevice,
} from '../../api/computers'
import { Modal } from '../common/Modal'
import { InlineStatusBanner } from '../common/InlineStatusBanner'
import { SettingsActionButton, SettingsStatusBadge } from '../agentSettings/SettingsControls'
import { SettingsSurface, SurfaceHeader, type SettingsSurfaceVariant } from '../common/SettingsSurface'

type ComputerConnectionsPanelProps = {
  url: string
  variant?: SettingsSurfaceVariant
}

function deviceState(device: ComputerDevice): { label: string; tone: 'success' | 'warning' | 'danger' | 'neutral' } {
  if (device.update_required) return { label: 'Update required', tone: 'danger' }
  if (device.paused) return { label: 'Paused', tone: 'warning' }
  if (device.online) return { label: 'Online', tone: 'success' }
  return { label: 'Offline', tone: 'neutral' }
}

function formatLastSeen(value: string | null): string {
  if (!value) return 'Never'
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
}

function PairingModal({
  url,
  pairingId,
  userCode,
  onClose,
  onApproved,
}: {
  url: string
  pairingId: string
  userCode: string
  onClose: () => void
  onApproved: () => void
}) {
  const pairingQuery = useQuery({
    queryKey: ['computer-pairing', pairingId],
    queryFn: () => fetchComputerPairing(url, pairingId),
  })
  const pairing = pairingQuery.data?.pairing
  const agents = pairingQuery.data?.agents ?? []
  const [agentId, setAgentId] = useState('')
  const [selectedAppOverrides, setSelectedAppOverrides] = useState<string[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const defaultSelectedApps = pairing?.apps
    .filter((app) => app.type === 'bundled')
    .map((app) => app.key) ?? []
  const selectedApps = selectedAppOverrides ?? defaultSelectedApps

  const approveMutation = useMutation({
    mutationFn: () => approveComputerPairing(url, pairingId, {
      user_code: userCode,
      agent_id: agentId,
      selected_app_keys: selectedApps,
    }),
    onSuccess: onApproved,
    onError: (mutationError) => setError(mutationError instanceof Error ? mutationError.message : 'Pairing failed.'),
  })

  return (
    <Modal
      title="Connect this computer"
      subtitle="Confirm the verification code, choose one agent, and approve the desktop apps it may use."
      icon={Monitor}
      onClose={onClose}
      footer={(
        <div className="flex justify-end gap-2">
          <SettingsActionButton surface="standalone" onClick={onClose}>Cancel</SettingsActionButton>
          <SettingsActionButton
            surface="standalone"
            tone="primary"
            disabled={!pairing || !agentId || approveMutation.isPending}
            onClick={() => approveMutation.mutate()}
          >
            {approveMutation.isPending ? 'Connecting…' : 'Connect computer'}
          </SettingsActionButton>
        </div>
      )}
    >
      {pairingQuery.isLoading ? <p className="text-sm text-slate-600">Loading pairing request…</p> : null}
      {pairingQuery.error ? (
        <InlineStatusBanner variant="error">This pairing request is unavailable or expired.</InlineStatusBanner>
      ) : null}
      {pairing ? (
        <div className="space-y-5">
          <div className="rounded-xl border border-blue-200 bg-blue-50 p-4">
            <div className="text-sm font-semibold text-blue-950">{pairing.display_name}</div>
            <div className="mt-1 text-xs text-blue-800">{pairing.platform} · {pairing.architecture} · v{pairing.client_version}</div>
            <div className="mt-3 font-mono text-2xl font-semibold tracking-[0.18em] text-blue-950">{userCode}</div>
          </div>
          <label className="block text-sm font-medium text-slate-800">
            Agent
            <select
              value={agentId}
              onChange={(event) => setAgentId(event.target.value)}
              className="mt-1 block w-full rounded-lg border-slate-300"
            >
              <option value="">Choose an agent</option>
              {agents.map((agent) => (
                <option key={agent.id} value={agent.id}>
                  {agent.organization_name ? `${agent.organization_name} · ` : 'Personal · '}{agent.name}
                </option>
              ))}
            </select>
          </label>
          <fieldset>
            <legend className="text-sm font-medium text-slate-800">Approved apps</legend>
            <div className="mt-2 space-y-2">
              {pairing.apps.map((app) => (
                <label key={app.key} className="flex items-start gap-3 rounded-lg border border-slate-200 p-3">
                  <input
                    type="checkbox"
                    checked={selectedApps.includes(app.key)}
                    onChange={(event) => setSelectedAppOverrides(
                      event.target.checked
                        ? [...selectedApps, app.key]
                        : selectedApps.filter((key) => key !== app.key),
                    )}
                    className="mt-0.5 rounded border-slate-300 text-blue-600"
                  />
                  <span>
                    <span className="block text-sm font-medium text-slate-900">{app.display_name}</span>
                    <span className="block text-xs text-slate-500">
                      {app.type === 'bundled' ? 'Bundled with computer.cpp' : 'Custom Lua app'}
                    </span>
                  </span>
                </label>
              ))}
            </div>
          </fieldset>
          {error ? <InlineStatusBanner variant="error">{error}</InlineStatusBanner> : null}
        </div>
      ) : null}
    </Modal>
  )
}

function ManageComputerModal({
  url,
  device,
  agents,
  onClose,
  onSaved,
}: {
  url: string
  device: ComputerDevice
  agents: Array<{ id: string; name: string; organization_name: string | null }>
  onClose: () => void
  onSaved: () => void
}) {
  const [name, setName] = useState(device.display_name)
  const [agentId, setAgentId] = useState(device.assignment?.agent_id ?? '')
  const [selectedApps, setSelectedApps] = useState(
    device.apps.filter((app) => app.approval_state === 'approved').map((app) => app.app_key),
  )
  const [error, setError] = useState<string | null>(null)
  const saveMutation = useMutation({
    mutationFn: () => updateComputer(url, device.id, {
      display_name: name,
      agent_id: agentId || undefined,
      approved_apps: device.apps
        .filter((app) => selectedApps.includes(app.app_key))
        .map((app) => ({ app_key: app.app_key, schema_sha256: app.schema_sha256 })),
    }),
    onSuccess: onSaved,
    onError: (mutationError) => setError(mutationError instanceof Error ? mutationError.message : 'Update failed.'),
  })
  return (
    <Modal
      title={`Manage ${device.display_name}`}
      subtitle="Changes apply only to this personal computer and its current agent grant."
      icon={Settings2}
      onClose={onClose}
      footer={(
        <div className="flex justify-end gap-2">
          <SettingsActionButton surface="standalone" onClick={onClose}>Cancel</SettingsActionButton>
          <SettingsActionButton
            surface="standalone"
            tone="primary"
            disabled={!name.trim() || !agentId || saveMutation.isPending}
            onClick={() => saveMutation.mutate()}
          >
            {saveMutation.isPending ? 'Saving…' : 'Save changes'}
          </SettingsActionButton>
        </div>
      )}
    >
      <div className="space-y-5">
        <label className="block text-sm font-medium text-slate-800">
          Computer name
          <input value={name} onChange={(event) => setName(event.target.value)} className="mt-1 block w-full rounded-lg border-slate-300" />
        </label>
        <label className="block text-sm font-medium text-slate-800">
          Agent
          <select value={agentId} onChange={(event) => setAgentId(event.target.value)} className="mt-1 block w-full rounded-lg border-slate-300">
            <option value="">Choose an agent</option>
            {agents.map((agent) => (
              <option key={agent.id} value={agent.id}>
                {agent.organization_name ? `${agent.organization_name} · ` : 'Personal · '}{agent.name}
              </option>
            ))}
          </select>
        </label>
        <fieldset>
          <legend className="text-sm font-medium text-slate-800">Approved apps</legend>
          <div className="mt-2 space-y-2">
            {device.apps.map((app) => (
              <label key={app.app_key} className="flex items-start gap-3 rounded-lg border border-slate-200 p-3">
                <input
                  type="checkbox"
                  disabled={!app.available}
                  checked={selectedApps.includes(app.app_key)}
                  onChange={(event) => setSelectedApps((current) => (
                    event.target.checked
                      ? [...current, app.app_key]
                      : current.filter((key) => key !== app.app_key)
                  ))}
                  className="mt-0.5 rounded border-slate-300 text-blue-600"
                />
                <span className="text-sm text-slate-900">
                  {app.display_name}
                  {app.approval_state === 'pending_approval' ? (
                    <span className="ml-2 text-xs font-medium text-amber-700">Updated schema requires approval</span>
                  ) : null}
                </span>
              </label>
            ))}
          </div>
        </fieldset>
        {error ? <InlineStatusBanner variant="error">{error}</InlineStatusBanner> : null}
      </div>
    </Modal>
  )
}

export function ComputerConnectionsPanel({ url, variant = 'embedded' }: ComputerConnectionsPanelProps) {
  const queryClient = useQueryClient()
  const queryKey = useMemo(() => ['computer-connections', url] as const, [url])
  const query = useQuery({ queryKey, queryFn: () => fetchComputers(url), refetchInterval: 30_000 })
  const [managedDevice, setManagedDevice] = useState<ComputerDevice | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const params = useMemo(() => new URLSearchParams(window.location.search), [])
  const pairingId = params.get('computer_pairing')
  const userCode = params.get('user_code') ?? ''
  const [showPairing, setShowPairing] = useState(Boolean(pairingId && userCode))
  const surface = variant
  const devices = query.data?.devices ?? []
  const agents = query.data?.agents ?? []

  const refresh = async (nextMessage?: string) => {
    await queryClient.invalidateQueries({ queryKey })
    if (nextMessage) setMessage(nextMessage)
  }
  const clearPairingUrl = () => {
    const next = new URL(window.location.href)
    next.searchParams.delete('computer_pairing')
    next.searchParams.delete('user_code')
    window.history.replaceState(window.history.state, '', `${next.pathname}${next.search}${next.hash}`)
  }

  if (query.isLoading || query.data?.enabled === false) return null
  if (query.error || !query.data?.downloads) {
    return <InlineStatusBanner variant="error" surface={surface}>Computer connections could not be loaded.</InlineStatusBanner>
  }

  const buttonSurface = variant
  const downloads = query.data.downloads
  const downloadButtons = [
    { key: 'macos', label: 'Download for Mac', icon: Apple, url: downloads.macos.url },
    { key: 'windows', label: 'Download for Windows', icon: Monitor, url: downloads.windows.url },
  ]
  const platform = navigator.platform.toLowerCase()
  if (platform.includes('win')) downloadButtons.reverse()

  return (
    <>
      <SettingsSurface variant={variant} roundedClassName={variant === 'embedded' ? 'rounded-xl' : 'rounded-2xl'}>
        <SurfaceHeader
          variant={variant}
          title="Connect your Agent to your computer"
          subtitle="computer.cpp makes an encrypted outbound connection to Gobii—no public IP, port forwarding, or inbound firewall rule is needed."
          actions={downloadButtons.map(({ key, label, icon: Icon, url: downloadUrl }, index) => (
            <SettingsActionButton
              key={key}
              as="a"
              href={downloadUrl}
              surface={buttonSurface}
              tone={index === 0 ? 'primary' : 'neutral'}
            >
              <Icon className="h-4 w-4" aria-hidden />
              {label}
              <Download className="h-3.5 w-3.5" aria-hidden />
            </SettingsActionButton>
          ))}
        />
        <div className={variant === 'embedded' ? 'px-6 pb-5 text-sm text-slate-300' : 'px-6 pb-5 text-sm text-slate-600'}>
          Install the app, choose “Connect to Gobii,” sign in, select an agent, and approve the desktop apps it may use.
          Minimum supported version: {downloads.minimum_version}.
        </div>
        {message ? (
          <div className="px-6 pb-4">
            <InlineStatusBanner variant="success" surface={surface} density="compact">{message}</InlineStatusBanner>
          </div>
        ) : null}
        {devices.length === 0 ? (
          <div className={variant === 'embedded' ? 'px-6 pb-6 text-sm text-slate-400' : 'px-6 pb-6 text-sm text-slate-600'}>
            No computers are connected yet.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full text-left text-sm">
              <thead className={variant === 'embedded' ? 'text-xs uppercase tracking-wide text-slate-400' : 'text-xs uppercase tracking-wide text-slate-500'}>
                <tr>
                  <th className="px-6 py-3 font-medium">Computer</th>
                  <th className="px-4 py-3 font-medium">Agent</th>
                  <th className="px-4 py-3 font-medium">Apps</th>
                  <th className="px-4 py-3 font-medium">Status</th>
                  <th className="px-6 py-3 text-right font-medium">Actions</th>
                </tr>
              </thead>
              <tbody>
                {devices.map((device) => {
                  const state = deviceState(device)
                  const approved = device.apps.filter((app) => app.approval_state === 'approved').length
                  const pending = device.apps.filter((app) => app.approval_state === 'pending_approval').length
                  return (
                    <tr key={device.id} className={variant === 'embedded' ? 'border-t border-slate-200/10' : 'border-t border-slate-200'}>
                      <td className="px-6 py-4">
                        <div className={variant === 'embedded' ? 'font-medium text-slate-100' : 'font-medium text-slate-900'}>{device.display_name}</div>
                        <div className={variant === 'embedded' ? 'mt-1 text-xs text-slate-400' : 'mt-1 text-xs text-slate-500'}>
                          {device.platform} · {device.architecture} · v{device.client_version}
                        </div>
                        {!device.permissions.can_manage_device ? (
                          <div className={variant === 'embedded' ? 'mt-1 text-xs text-slate-400' : 'mt-1 text-xs text-slate-500'}>
                            Owned by {device.owner.display_name}
                          </div>
                        ) : null}
                      </td>
                      <td className={variant === 'embedded' ? 'px-4 py-4 text-slate-300' : 'px-4 py-4 text-slate-700'}>
                        {device.assignment?.organization_name ? `${device.assignment.organization_name} · ` : ''}
                        {device.assignment?.agent_name ?? 'Unassigned'}
                      </td>
                      <td className={variant === 'embedded' ? 'px-4 py-4 text-slate-300' : 'px-4 py-4 text-slate-700'}>
                        {approved} approved{pending ? ` · ${pending} pending` : ''}
                      </td>
                      <td className="px-4 py-4">
                        <SettingsStatusBadge surface={surface} tone={state.tone}>{state.label}</SettingsStatusBadge>
                        <div className={variant === 'embedded' ? 'mt-1 text-xs text-slate-500' : 'mt-1 text-xs text-slate-500'}>
                          Last seen {formatLastSeen(device.last_seen_at)}
                        </div>
                      </td>
                      <td className="px-6 py-4">
                        <div className="flex justify-end gap-2">
                          {device.permissions.can_manage_device ? (
                            <>
                              <SettingsActionButton
                                surface={buttonSurface}
                                size="sm"
                                onClick={() => updateComputer(url, device.id, { paused: !device.paused }).then(() => refresh(device.paused ? 'Computer resumed.' : 'Computer paused.'))}
                              >
                                {device.paused ? <Play className="h-3.5 w-3.5" /> : <Pause className="h-3.5 w-3.5" />}
                                {device.paused ? 'Resume' : 'Pause'}
                              </SettingsActionButton>
                              <SettingsActionButton surface={buttonSurface} size="sm" onClick={() => setManagedDevice(device)}>
                                <Settings2 className="h-3.5 w-3.5" /> Manage
                              </SettingsActionButton>
                              {device.assignment ? (
                                <SettingsActionButton
                                  surface={buttonSurface}
                                  size="sm"
                                  onClick={() => revokeComputerAssignment(url, device.id).then(() => refresh('Computer unassigned.'))}
                                >
                                  Unassign
                                </SettingsActionButton>
                              ) : null}
                              <SettingsActionButton
                                surface={buttonSurface}
                                size="sm"
                                tone="danger"
                                onClick={() => {
                                  if (window.confirm(`Revoke ${device.display_name}? It will need to be paired again.`)) {
                                    void revokeComputer(url, device.id).then(() => refresh('Computer revoked.'))
                                  }
                                }}
                              >
                                <Trash2 className="h-3.5 w-3.5" /> Revoke
                              </SettingsActionButton>
                            </>
                          ) : (
                            <SettingsActionButton
                              surface={buttonSurface}
                              size="sm"
                              tone="danger"
                              onClick={() => revokeComputerAssignment(url, device.id).then(() => refresh('Computer grant revoked.'))}
                            >
                              Remove grant
                            </SettingsActionButton>
                          )}
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </SettingsSurface>
      {showPairing && pairingId && userCode ? (
        <PairingModal
          url={url}
          pairingId={pairingId}
          userCode={userCode}
          onClose={() => {
            setShowPairing(false)
            clearPairingUrl()
          }}
          onApproved={() => {
            setShowPairing(false)
            clearPairingUrl()
            void refresh('Computer approved. The desktop app will finish connecting.')
          }}
        />
      ) : null}
      {managedDevice ? (
        <ManageComputerModal
          url={url}
          device={managedDevice}
          agents={agents}
          onClose={() => setManagedDevice(null)}
          onSaved={() => {
            setManagedDevice(null)
            void refresh('Computer settings updated.')
          }}
        />
      ) : null}
    </>
  )
}
