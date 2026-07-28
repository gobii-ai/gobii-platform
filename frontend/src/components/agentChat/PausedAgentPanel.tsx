import { CirclePause, Loader2, RotateCcw } from 'lucide-react'

import { AgentChatButton, AgentChatSurface } from './uiPrimitives'

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
    <AgentChatSurface
      as="section"
      tone="warning"
      className="pointer-events-auto flex w-full max-w-4xl flex-wrap items-start gap-3 px-4 py-3 text-amber-950 sm:flex-nowrap sm:items-center"
      aria-labelledby="agent-paused-title"
    >
      <CirclePause className="h-5 w-5 shrink-0 text-amber-700" aria-hidden="true" />
      <div className="min-w-0 flex-1">
        <p id="agent-paused-title" className="m-0 text-sm font-bold">This agent is paused</p>
        <p className="m-0 mt-0.5 text-[0.8125rem] leading-snug">
          This agent is paused and won’t respond or do work until it’s reactivated.
        </p>
        {!canReactivate ? (
          <p className="m-0 mt-0.5 text-[0.8125rem] font-semibold leading-snug">
            Ask an owner or admin to reactivate it.
          </p>
        ) : null}
        {error ? (
          <p className="m-0 mt-1 text-xs font-semibold text-red-700" role="alert">{error}</p>
        ) : null}
      </div>
      {canReactivate ? (
        <AgentChatButton
          className="ml-8 shrink-0 sm:ml-0"
          tone="warning"
          size="sm"
          onClick={() => void onReactivate?.()}
          disabled={reactivating}
        >
          {reactivating ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
          ) : (
            <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
          )}
          {reactivating ? 'Reactivating…' : 'Reactivate agent'}
        </AgentChatButton>
      ) : null}
    </AgentChatSurface>
  )
}
