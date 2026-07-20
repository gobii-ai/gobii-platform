import { Globe } from 'lucide-react'

import type { PendingRequestedSecretsAction } from '../../types/agentChat'
import { HoverInfoButton } from './InlineInfoTooltipButton'
import { PendingRequestReviewFooter } from './PendingRequestPanelParts'

type PendingRequestedSecretsPanelProps = {
  action: PendingRequestedSecretsAction
  disabled?: boolean
  busyAction?: 'save' | 'remove' | null
  error?: string | null
  secretValues: Record<string, string>
  makeGlobal: boolean
  showReviewSummary?: boolean
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
  showReviewSummary = true,
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
    <div className="w-full space-y-3">
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
              <HoverInfoButton
                label="What Global does"
                description="Makes this value available across agents in the current scope instead of storing it only for this agent."
                disabled={disabled || busyAction !== null}
              />
            </label>
          </div>
        </div>
      </div>

      <PendingRequestReviewFooter
        description="You're allowing this agent to use this credential."
        showSummary={showReviewSummary}
        disabled={disabled}
        busy={busyAction !== null}
        secondaryLabel="Remove"
        secondaryBusyLabel={busyAction === 'remove' ? 'Removing...' : 'Remove'}
        primaryLabel="Save"
        primaryBusyLabel={busyAction === 'save' ? 'Saving...' : 'Save'}
        theme="secret"
        error={error}
        onSecondary={() => void onRemove()}
        onPrimary={() => void onSave()}
      />
    </div>
  )
}
