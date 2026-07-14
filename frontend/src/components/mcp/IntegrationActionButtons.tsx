import { Loader2, Plug, Unplug, Users } from 'lucide-react'

import { useSettingsSurfaceVariant } from '../common/SettingsSurface'

type ConnectionKind = 'connect' | 'disconnect' | 'picker' | null

export function IntegrationConnectionButton({
  connected,
  pendingKind,
  disabled,
  onConnect,
  onDisconnect,
  disconnectTone = 'danger',
  minWidth = true,
}: {
  connected: boolean
  pendingKind: ConnectionKind
  disabled: boolean
  onConnect?: () => void
  onDisconnect?: () => void
  disconnectTone?: 'danger' | 'neutral'
  minWidth?: boolean
}) {
  const surface = useSettingsSurfaceVariant()
  const action = connected ? 'disconnect' : 'connect'
  const className = action === 'connect'
    ? surface === 'embedded'
      ? 'border border-sky-300/25 bg-sky-900/55 text-sky-50 hover:border-sky-200/40 hover:bg-sky-900/75'
      : 'bg-blue-600 text-white hover:bg-blue-700'
    : surface === 'embedded'
      ? disconnectTone === 'danger'
        ? 'border-rose-300/25 bg-rose-950/20 text-rose-200 hover:border-rose-200/40 hover:bg-rose-900/35'
        : 'border-slate-200/20 bg-slate-950/20 text-slate-300 hover:border-slate-100/35 hover:bg-slate-900/40'
      : disconnectTone === 'danger'
        ? 'border-red-200 bg-white text-red-700 hover:bg-red-50'
        : 'border-slate-200 bg-white text-slate-700 hover:bg-slate-50'
  const isPending = pendingKind === action
  const Icon = connected ? Unplug : Plug
  const onClick = connected ? onDisconnect : onConnect

  return (
    <button
      type="button"
      className={[
        `inline-flex items-center justify-center gap-2 rounded-md px-3 py-2 text-sm font-semibold transition disabled:opacity-60 ${connected ? 'border' : ''} ${className}`,
        minWidth ? 'min-w-28' : '',
      ].filter(Boolean).join(' ')}
      onClick={onClick}
      disabled={disabled || !onClick}
    >
      {isPending ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <Icon className="h-4 w-4" aria-hidden="true" />}
      {connected ? 'Disconnect' : 'Connect'}
    </button>
  )
}

export function IntegrationManageButton({ disabled, onClick }: { disabled: boolean; onClick: () => void }) {
  const surface = useSettingsSurfaceVariant()
  const className = surface === 'embedded'
    ? 'border border-sky-300/25 bg-sky-900/55 text-sky-50 hover:border-sky-200/40 hover:bg-sky-900/75'
    : 'bg-blue-600 text-white hover:bg-blue-700'
  return (
    <button
      type="button"
      className={`inline-flex min-w-28 items-center justify-center gap-2 rounded-md px-3 py-2 text-sm font-semibold transition disabled:opacity-60 ${className}`}
      onClick={onClick}
      disabled={disabled}
    >
      <Users className="h-4 w-4" aria-hidden="true" />
      Manage
    </button>
  )
}
