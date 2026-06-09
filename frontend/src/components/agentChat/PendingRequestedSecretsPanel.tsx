import { createPortal } from 'react-dom'
import { Globe } from 'lucide-react'

import type { PendingRequestedSecretsAction } from '../../types/agentChat'
import { InlineInfoTooltipButton } from './InlineInfoTooltipButton'

type PendingRequestedSecretsPanelProps = {
  action: PendingRequestedSecretsAction
  disabled?: boolean
  busyAction?: 'save' | 'remove' | null
  error?: string | null
  secretValues: Record<string, string>
  makeGlobal: boolean
  onSecretValueChange: (secretId: string, value: string) => void
  onMakeGlobalChange: (checked: boolean) => void
  onSave: () => Promise<void> | void
  onRemove: () => Promise<void> | void
  actionsContainer?: Element | null
  suppressInlineActions?: boolean
}

export function PendingRequestedSecretsPanel({
  action,
  disabled = false,
  busyAction = null,
  error = null,
  secretValues,
  makeGlobal,
  onSecretValueChange,
  onMakeGlobalChange,
  onSave,
  onRemove,
  actionsContainer = null,
  suppressInlineActions = false,
}: PendingRequestedSecretsPanelProps) {
  const secret = action.secrets[0]

  if (!secret) {
    return null
  }

  const actionRow = (
    <div className="space-y-2">
      <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-start">
        <button
          type="button"
          disabled={disabled || busyAction !== null}
          className="inline-flex w-full items-center justify-center rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-700 transition hover:border-slate-400 hover:text-slate-900 disabled:cursor-not-allowed disabled:opacity-60 sm:w-32"
          onClick={() => void onRemove()}
        >
          {busyAction === 'remove' ? 'Removing...' : 'Remove'}
        </button>
        <button
          type="button"
          disabled={disabled || busyAction !== null}
          className="inline-flex w-full items-center justify-center rounded-lg bg-sky-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-sky-700 disabled:cursor-not-allowed disabled:opacity-60 sm:w-32"
          onClick={() => void onSave()}
        >
          {busyAction === 'save' ? 'Saving...' : 'Save'}
        </button>
      </div>
      {error ? <p className="text-sm text-rose-600 sm:text-right">{error}</p> : null}
    </div>
  )

  return (
    <div className="max-w-2xl space-y-3">
      <div className="space-y-3">
        {secret.description ? (
          <div>
            <p className="text-xs font-semibold text-slate-900">
              {secret.secretType === 'env_var' ? 'Environment variable' : 'Credential'}
            </p>
            <p className="mt-1 text-sm leading-5 text-slate-700">{secret.description}</p>
          </div>
        ) : null}

        <div className="space-y-2">
          <label htmlFor={`pending-secret-${secret.id}`} className="text-xs font-semibold text-slate-900">
            Value
          </label>
          <div className="space-y-2">
            <input
              id={`pending-secret-${secret.id}`}
              type="password"
              value={secretValues[secret.id] ?? ''}
              onChange={(event) => onSecretValueChange(secret.id, event.currentTarget.value)}
              disabled={disabled || busyAction !== null}
              className="block w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 shadow-sm focus:border-sky-500 focus:outline-none focus:ring-2 focus:ring-sky-200"
              placeholder={`Enter value for ${secret.name}`}
              autoComplete="new-password"
            />
            <label className="inline-flex min-h-8 w-fit items-center gap-2 rounded-lg border border-white/70 bg-white/48 px-2.5 py-1.5 text-sm text-slate-700">
              <input
                type="checkbox"
                checked={makeGlobal}
                onChange={(event) => onMakeGlobalChange(event.currentTarget.checked)}
                disabled={disabled || busyAction !== null}
                className="h-4 w-4 rounded border-slate-300 text-sky-600 focus:ring-sky-500"
              />
              <Globe className="h-4 w-4 text-sky-600" aria-hidden="true" />
              <span>Make global</span>
              <InlineInfoTooltipButton
                label="What Global does"
                description="Makes this value available across agents in the current scope instead of storing it only for this agent."
                disabled={disabled || busyAction !== null}
              />
            </label>
          </div>
        </div>
      </div>

      {actionsContainer ? createPortal(actionRow, actionsContainer) : suppressInlineActions ? null : actionRow}
    </div>
  )
}
