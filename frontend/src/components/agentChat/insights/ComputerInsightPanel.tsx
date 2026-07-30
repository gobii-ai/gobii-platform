import { useQuery } from '@tanstack/react-query'
import { Monitor } from 'lucide-react'

import { fetchComputers } from '../../../api/computers'

export function ComputerInsightPanel({
  agentId = null,
  computerConnectionsUrl = null,
}: {
  agentId?: string | null
  computerConnectionsUrl?: string | null
}) {
  const query = useQuery({
    queryKey: ['computer-connections', computerConnectionsUrl, agentId],
    queryFn: () => fetchComputers(computerConnectionsUrl as string, agentId),
    enabled: Boolean(computerConnectionsUrl && agentId),
    refetchInterval: 30_000,
  })
  const devices = query.data?.devices ?? []
  return (
    <div className="space-y-3 text-sm">
      <div className="flex items-center justify-between gap-3">
        <span className="font-semibold text-slate-100">Connected computers</span>
        <a href="/app/integrations" className="text-xs font-semibold text-sky-300 hover:text-sky-200">Manage</a>
      </div>
      {!computerConnectionsUrl ? <p className="text-slate-400">Computer connections are unavailable.</p> : null}
      {query.isLoading ? <p className="text-slate-400">Loading computers…</p> : null}
      {query.error ? <p className="text-rose-300">Computer status could not be loaded.</p> : null}
      {!query.isLoading && !query.error && devices.length === 0 ? (
        <p className="text-slate-400">No computer is assigned to this agent.</p>
      ) : null}
      {devices.map((device) => (
        <div key={device.id} className="flex items-start gap-3 rounded-lg border border-slate-200/15 bg-slate-950/20 p-3">
          <Monitor className="mt-0.5 h-4 w-4 text-sky-300" aria-hidden />
          <div className="min-w-0">
            <div className="font-medium text-slate-100">{device.display_name}</div>
            <div className="mt-0.5 text-xs text-slate-400">
              {device.paused ? 'Paused' : device.online ? 'Online' : 'Offline'}
              {' · '}
              {device.apps.filter((app) => app.approval_state === 'approved').map((app) => app.display_name).join(', ') || 'No approved apps'}
            </div>
          </div>
        </div>
      ))}
    </div>
  )
}
