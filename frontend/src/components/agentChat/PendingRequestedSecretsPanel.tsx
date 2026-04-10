import { Globe } from 'lucide-react'

import type { PendingRequestedSecretsAction } from '../../types/agentChat'
import { InlineInfoTooltipButton } from './InlineInfoTooltipButton'
import { PendingActionSectionCard } from './PendingActionSectionCard'

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
}: PendingRequestedSecretsPanelProps) {
  const secret = action.secrets[0]

  if (!secret) {
    return null
  }

  return (
    <PendingActionSectionCard toneClass="border-sky-200 bg-sky-50/55">
      <div className="space-y-3">
        <div className="rounded-xl bg-white px-3 py-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="rounded-full bg-sky-100 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-[0.12em] text-sky-700">
                {secret.secretType === 'env_var' ? 'Env Var' : 'Credential'}
              </span>
              {secret.description ? <p className="text-sm text-slate-700">{secret.description}</p> : null}
            </div>
            <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:items-center">
              <input
                type="password"
                value={secretValues[secret.id] ?? ''}
                onChange={(event) => onSecretValueChange(secret.id, event.currentTarget.value)}
                disabled={disabled || busyAction !== null}
                className="block w-full rounded-xl border border-slate-300 px-3 py-2 text-sm text-slate-900 shadow-sm focus:border-sky-500 focus:outline-none focus:ring-2 focus:ring-sky-200"
                placeholder={`Enter value for ${secret.name}`}
                autoComplete="new-password"
              />
              <label className="inline-flex shrink-0 items-center gap-2 rounded-xl border border-slate-200 px-3 py-2 text-sm text-slate-700">
                <input
                  type="checkbox"
                  checked={makeGlobal}
                  onChange={(event) => onMakeGlobalChange(event.currentTarget.checked)}
                  disabled={disabled || busyAction !== null}
                  className="h-4 w-4 rounded border-slate-300 text-sky-600 focus:ring-sky-500"
                />
                <Globe className="h-4 w-4 text-sky-600" aria-hidden="true" />
                <span>Global</span>
                <InlineInfoTooltipButton
                  label="What Global does"
                  description="Makes this value available across agents in the current scope instead of storing it only for this agent."
                  disabled={disabled || busyAction !== null}
                />
              </label>
            </div>
          </div>
        </div>
        <div className="grid grid-cols-2 gap-2">
          <button
            type="button"
            disabled={disabled || busyAction !== null}
            className="inline-flex w-full items-center justify-center rounded-xl border border-slate-300 bg-white px-3 py-2.5 text-sm font-semibold text-slate-700 transition hover:border-slate-400 hover:text-slate-900 disabled:cursor-not-allowed disabled:opacity-60"
            onClick={() => void onRemove()}
          >
            {busyAction === 'remove' ? 'Removing...' : 'Remove'}
          </button>
          <button
            type="button"
            disabled={disabled || busyAction !== null}
            className="inline-flex w-full items-center justify-center rounded-xl bg-sky-600 px-3 py-2.5 text-sm font-semibold text-white transition hover:bg-sky-700 disabled:cursor-not-allowed disabled:opacity-60"
            onClick={() => void onSave()}
          >
            {busyAction === 'save' ? 'Saving...' : 'Save'}
          </button>
        </div>
        {error ? <p className="text-sm text-rose-600">{error}</p> : null}
      </div>
    </PendingActionSectionCard>
  )
}
