import { CirclePause, Loader2, RotateCcw } from 'lucide-react'

import { AgentChatButton } from './uiPrimitives'

type PausedAgentPanelProps = {
  canReactivate: boolean
  reactivating?: boolean
  error?: string | null
  onReactivate?: () => void | Promise<void>
}

export function PausedAgentPanel({
  canReactivate,
  reactivating = false,
  error = null,
  onReactivate,
}: PausedAgentPanelProps) {
  return (
    <section className="agent-paused-panel" aria-labelledby="agent-paused-title">
      <CirclePause className="agent-paused-panel__icon" aria-hidden="true" />
      <div className="agent-paused-panel__body">
        <p id="agent-paused-title" className="agent-paused-panel__title">This agent is paused</p>
        <p className="agent-paused-panel__message">
          This agent is paused and won’t respond or do work until it’s reactivated.
        </p>
        {!canReactivate ? (
          <p className="agent-paused-panel__guidance">
            Ask an owner or admin to reactivate it.
          </p>
        ) : null}
        {error ? (
          <p className="agent-paused-panel__error" role="alert">{error}</p>
        ) : null}
      </div>
      {canReactivate ? (
        <AgentChatButton
          className="agent-paused-panel__action"
          variant="solid"
          size="sm"
          onClick={() => void onReactivate?.()}
          disabled={reactivating}
        >
          {reactivating ? (
            <Loader2 className="agent-paused-panel__spinner" aria-hidden="true" />
          ) : (
            <RotateCcw aria-hidden="true" />
          )}
          {reactivating ? 'Reactivating…' : 'Reactivate agent'}
        </AgentChatButton>
      ) : null}
    </section>
  )
}
