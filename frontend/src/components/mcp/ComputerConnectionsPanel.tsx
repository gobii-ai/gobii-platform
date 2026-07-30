import { useMemo, useState, type SVGProps } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
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
  getComputerConnectionsUrl,
  revokeComputer,
  revokeComputerAssignment,
  updateComputer,
  type ComputerAgent,
  type ComputerDevice,
} from '../../api/computers'
import { InlineStatusBanner } from '../common/InlineStatusBanner'
import { ModalForm } from '../common/ModalForm'
import { SettingsActionButton, SettingsStatusBadge } from '../agentSettings/SettingsControls'
import { SettingsSurface, SurfaceHeader, type SettingsSurfaceVariant } from '../common/SettingsSurface'

function AppleLogo(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" {...props}>
      <path d="M17.05 20.28c-.98.95-2.05.8-3.08.35-1.09-.46-2.09-.48-3.24 0-1.44.62-2.2.44-3.06-.35C2.79 15.25 3.51 7.59 9.05 7.31c1.35.07 2.29.74 3.08.79 1.18-.24 2.31-.93 3.57-.84 1.51.12 2.65.72 3.4 1.8-3.12 1.87-2.38 5.98.48 7.13-.57 1.5-1.31 2.99-2.53 4.09ZM12.03 7.25C11.88 5.02 13.69 3.18 15.77 3c.29 2.58-2.34 4.5-3.74 4.25Z" />
    </svg>
  )
}

function WindowsLogo(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" {...props}>
      <path d="M3 5.5 10.5 4.5V11H3V5.5Zm8.5-1.15L21 3v8h-9.5V4.35ZM3 12h7.5v6.5L3 17.5V12Zm8.5 0H21v8l-9.5-1.35V12Z" />
    </svg>
  )
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

function AgentSelect({
  agents,
  value,
  onChange,
}: {
  agents: ComputerAgent[]
  value: string
  onChange: (value: string) => void
}) {
  return (
    <label className="block text-sm font-medium text-slate-800">
      Agent
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
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
  )
}

type ApprovalOption = {
  key: string
  name: string
  detail?: string
  disabled?: boolean
}

function AppApprovalChecklist({
  apps,
  selected,
  onChange,
}: {
  apps: ApprovalOption[]
  selected: string[]
  onChange: (selected: string[]) => void
}) {
  return (
    <fieldset>
      <legend className="text-sm font-medium text-slate-800">Approved apps</legend>
      <div className="mt-2 space-y-2">
        {apps.map((app) => (
          <label key={app.key} className="flex items-start gap-3 rounded-lg border border-slate-200 p-3">
            <input
              type="checkbox"
              disabled={app.disabled}
              checked={selected.includes(app.key)}
              onChange={(event) => onChange(
                event.target.checked
                  ? [...selected, app.key]
                  : selected.filter((key) => key !== app.key),
              )}
              className="mt-0.5 rounded border-slate-300 text-blue-600"
            />
            <span>
              <span className="block text-sm font-medium text-slate-900">{app.name}</span>
              {app.detail ? <span className="block text-xs text-slate-500">{app.detail}</span> : null}
            </span>
          </label>
        ))}
      </div>
    </fieldset>
  )
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
  })
  const error = approveMutation.error instanceof Error ? approveMutation.error.message : null

  return (
    <ModalForm
      id="approve-computer-pairing"
      title="Connect this computer"
      subtitle="Confirm the verification code, choose one agent, and approve the desktop apps it may use."
      icon={Monitor}
      onClose={onClose}
      onSubmit={(event) => {
        event.preventDefault()
        approveMutation.mutate()
      }}
      submitLabel="Connect computer"
      submittingLabel="Connecting…"
      submitting={approveMutation.isPending}
      submitDisabled={!pairing || !agentId}
      errorMessages={error ? [error] : null}
      formClassName="space-y-5"
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
          <AgentSelect agents={agents} value={agentId} onChange={setAgentId} />
          <AppApprovalChecklist
            apps={pairing.apps.map((app) => ({
              key: app.key,
              name: app.display_name,
              detail: app.type === 'bundled' ? 'Bundled with computer.cpp' : 'Custom Lua app',
            }))}
            selected={selectedApps}
            onChange={setSelectedAppOverrides}
          />
        </div>
      ) : null}
    </ModalForm>
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
  agents: ComputerAgent[]
  onClose: () => void
  onSaved: () => void
}) {
  const [name, setName] = useState(device.display_name)
  const [agentId, setAgentId] = useState(device.assignment?.agent_id ?? '')
  const [selectedApps, setSelectedApps] = useState(
    device.apps.filter((app) => app.approval_state === 'approved').map((app) => app.app_key),
  )
  const saveMutation = useMutation({
    mutationFn: () => updateComputer(url, device.id, {
      display_name: name,
      agent_id: agentId || undefined,
      approved_apps: device.apps
        .filter((app) => app.available && selectedApps.includes(app.app_key))
        .map((app) => ({ app_key: app.app_key, schema_sha256: app.schema_sha256 })),
    }),
    onSuccess: onSaved,
  })
  const error = saveMutation.error instanceof Error ? saveMutation.error.message : null
  return (
    <ModalForm
      id="manage-computer"
      title={`Manage ${device.display_name}`}
      subtitle="Changes apply only to this personal computer and its current agent grant."
      icon={Settings2}
      onClose={onClose}
      onSubmit={(event) => {
        event.preventDefault()
        saveMutation.mutate()
      }}
      submitLabel="Save changes"
      submitting={saveMutation.isPending}
      submitDisabled={!name.trim() || !agentId}
      errorMessages={error ? [error] : null}
      formClassName="space-y-5"
    >
      <label className="block text-sm font-medium text-slate-800">
        Computer name
        <input value={name} onChange={(event) => setName(event.target.value)} className="mt-1 block w-full rounded-lg border-slate-300" />
      </label>
      <AgentSelect agents={agents} value={agentId} onChange={setAgentId} />
      <AppApprovalChecklist
        apps={device.apps.map((app) => ({
          key: app.app_key,
          name: app.display_name,
          detail: app.approval_state === 'pending_approval'
            ? 'Updated schema requires approval'
            : !app.available ? 'Unavailable' : undefined,
          disabled: !app.available,
        }))}
        selected={selectedApps}
        onChange={setSelectedApps}
      />
    </ModalForm>
  )
}

export function ComputerConnectionsPanel({ variant = 'embedded' }: { variant?: SettingsSurfaceVariant }) {
  const url = getComputerConnectionsUrl()
  const queryClient = useQueryClient()
  const queryKey = useMemo(() => ['computer-connections', url] as const, [url])
  const query = useQuery({
    queryKey,
    queryFn: () => fetchComputers(url as string),
    enabled: Boolean(url),
    refetchInterval: 30_000,
  })
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
  const actionMutation = useMutation({
    mutationFn: async ({
      request,
      successMessage,
    }: {
      request: () => Promise<unknown>
      successMessage: string
    }) => {
      await request()
      return successMessage
    },
    onMutate: () => setMessage(null),
    onSuccess: (successMessage) => {
      void refresh(successMessage)
    },
  })
  const actionError = actionMutation.error instanceof Error ? actionMutation.error.message : null
  const clearPairingUrl = () => {
    const next = new URL(window.location.href)
    next.searchParams.delete('computer_pairing')
    next.searchParams.delete('user_code')
    window.history.replaceState(window.history.state, '', `${next.pathname}${next.search}${next.hash}`)
  }

  if (!url || query.isLoading || query.data?.enabled === false) return null
  if (query.error || !query.data?.downloads) {
    return <InlineStatusBanner variant="error" surface={surface}>Computer connections could not be loaded.</InlineStatusBanner>
  }

  const buttonSurface = variant
  const downloads = query.data.downloads
  const downloadButtons = [
    { key: 'macos', label: 'Download for Mac', icon: AppleLogo, url: downloads.macos.url },
    { key: 'windows', label: 'Download for Windows', icon: WindowsLogo, url: downloads.windows.url },
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
        {actionError ? (
          <div className="px-6 pb-4">
            <InlineStatusBanner variant="error" surface={surface} density="compact">{actionError}</InlineStatusBanner>
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
                                disabled={actionMutation.isPending}
                                onClick={() => actionMutation.mutate({
                                  request: () => updateComputer(url, device.id, { paused: !device.paused }),
                                  successMessage: device.paused ? 'Computer resumed.' : 'Computer paused.',
                                })}
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
                                  disabled={actionMutation.isPending}
                                  onClick={() => actionMutation.mutate({
                                    request: () => revokeComputerAssignment(url, device.id),
                                    successMessage: 'Computer unassigned.',
                                  })}
                                >
                                  Unassign
                                </SettingsActionButton>
                              ) : null}
                              <SettingsActionButton
                                surface={buttonSurface}
                                size="sm"
                                tone="danger"
                                disabled={actionMutation.isPending}
                                onClick={() => {
                                  if (window.confirm(`Revoke ${device.display_name}? It will need to be paired again.`)) {
                                    actionMutation.mutate({
                                      request: () => revokeComputer(url, device.id),
                                      successMessage: 'Computer revoked.',
                                    })
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
                              disabled={actionMutation.isPending}
                              onClick={() => actionMutation.mutate({
                                request: () => revokeComputerAssignment(url, device.id),
                                successMessage: 'Computer grant revoked.',
                              })}
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
