import { MessageSquareQuote, Sparkles } from 'lucide-react'

import type { PendingHumanInputRequest } from '../../types/agentChat'

type HumanInputComposerPanelProps = {
  requests: PendingHumanInputRequest[]
  activeRequestId: string | null
  armedRequestId?: string | null
  disabled?: boolean
  busyRequestId?: string | null
  onActiveRequestChange: (requestId: string) => void
  onSelectOption: (requestId: string, optionKey: string) => Promise<void> | void
  onArmComposer: (requestId: string) => void
  onDisarmComposer?: () => void
}

function formatChannelLabel(channel: string | null | undefined): string | null {
  switch ((channel || '').toLowerCase()) {
    case 'email':
      return 'Email reply'
    case 'sms':
      return 'SMS reply'
    case 'web':
      return 'Web chat reply'
    default:
      return null
  }
}

export function HumanInputComposerPanel({
  requests,
  activeRequestId,
  armedRequestId = null,
  disabled = false,
  busyRequestId = null,
  onActiveRequestChange,
  onSelectOption,
  onArmComposer,
  onDisarmComposer,
}: HumanInputComposerPanelProps) {
  if (!requests.length) {
    return null
  }

  const activeRequest = requests.find((request) => request.id === activeRequestId) ?? requests[0]
  const channelLabel = formatChannelLabel(activeRequest.activeConversationChannel)
  const isFreeTextOnly = activeRequest.inputMode === 'free_text_only' || activeRequest.options.length === 0
  const isArmed = armedRequestId === activeRequest.id

  return (
    <section
      className="rounded-[1.15rem] border border-amber-200/80 bg-[linear-gradient(140deg,_rgba(255,247,237,0.98)_0%,_rgba(255,251,235,0.98)_48%,_rgba(255,244,214,0.98)_100%)] px-4 py-4 text-slate-800"
      aria-label="Pending human input request"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="inline-flex items-center gap-1 rounded-full bg-amber-500 px-2.5 py-1 text-[0.68rem] font-semibold uppercase tracking-[0.18em] text-white">
              <Sparkles className="h-3 w-3" aria-hidden="true" />
              Needs input
            </span>
            {activeRequest.referenceCode ? (
              <span className="rounded-full bg-white/80 px-2.5 py-1 text-[0.72rem] font-semibold text-amber-800">
                Ref {activeRequest.referenceCode}
              </span>
            ) : null}
            {channelLabel ? (
              <span className="rounded-full bg-amber-100/80 px-2.5 py-1 text-[0.72rem] font-medium text-amber-900">
                {channelLabel}
              </span>
            ) : null}
          </div>
          <h3 className="mt-3 text-base font-semibold tracking-[-0.02em] text-slate-900">
            {activeRequest.title}
          </h3>
          <p className="mt-1 whitespace-pre-line text-sm leading-6 text-slate-700">
            {activeRequest.question}
          </p>
        </div>
        {requests.length > 1 ? (
          <div className="flex max-w-full flex-wrap gap-2">
            {requests.map((request, index) => {
              const isActive = request.id === activeRequest.id
              return (
                <button
                  key={request.id}
                  type="button"
                  className={`rounded-full px-3 py-1.5 text-xs font-semibold transition ${
                    isActive
                      ? 'bg-slate-900 text-white'
                      : 'bg-white/80 text-slate-700 hover:bg-white'
                  }`}
                  onClick={() => onActiveRequestChange(request.id)}
                  disabled={disabled}
                >
                  Q{requests.length - index}
                </button>
              )
            })}
          </div>
        ) : null}
      </div>

      {isFreeTextOnly ? (
        <div className="mt-4 rounded-2xl bg-white/70 px-4 py-3 text-sm leading-6 text-slate-700">
          <div className="flex items-center gap-2 font-semibold text-slate-900">
            <MessageSquareQuote className="h-4 w-4 text-amber-700" aria-hidden="true" />
            Reply in your own words
          </div>
          <p className="mt-1">
            Use the composer only after you explicitly arm this request.
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <button
              type="button"
              className={`rounded-full px-3 py-1.5 text-xs font-semibold transition ${
                isArmed
                  ? 'bg-slate-900 text-white'
                  : 'bg-amber-500 text-white hover:bg-amber-600'
              }`}
              onClick={() => onArmComposer(activeRequest.id)}
              disabled={disabled}
            >
              {isArmed ? 'Answer mode active' : 'Answer in composer'}
            </button>
            {isArmed && onDisarmComposer ? (
              <button
                type="button"
                className="rounded-full bg-white/85 px-3 py-1.5 text-xs font-semibold text-slate-700 transition hover:bg-white"
                onClick={onDisarmComposer}
                disabled={disabled}
              >
                Send normal message instead
              </button>
            ) : null}
          </div>
        </div>
      ) : (
        <div className="mt-4 grid gap-2">
          {activeRequest.options.map((option, index) => {
            const isBusy = busyRequestId === activeRequest.id
            return (
              <button
                key={option.key}
                type="button"
                onClick={() => void onSelectOption(activeRequest.id, option.key)}
                disabled={disabled || isBusy}
                className="group rounded-[1rem] border border-amber-200/90 bg-white/88 px-4 py-3 text-left transition hover:border-amber-300 hover:bg-white disabled:cursor-wait disabled:opacity-70"
              >
                <div className="flex items-start gap-3">
                  <span className="mt-0.5 inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-amber-500 text-xs font-semibold text-white">
                    {index + 1}
                  </span>
                  <div className="min-w-0">
                    <div className="text-sm font-semibold text-slate-900">{option.title}</div>
                    <div className="mt-1 text-sm leading-5 text-slate-600">{option.description}</div>
                  </div>
                </div>
              </button>
            )
          })}
          <div className="rounded-2xl bg-white/70 px-4 py-3 text-sm leading-6 text-slate-700">
            <div className="font-semibold text-slate-900">Need a custom answer?</div>
            <p className="mt-1">Arm this request first so your next composer message is treated as the answer.</p>
            <div className="mt-3 flex flex-wrap gap-2">
              <button
                type="button"
                className={`rounded-full px-3 py-1.5 text-xs font-semibold transition ${
                  isArmed
                    ? 'bg-slate-900 text-white'
                    : 'bg-amber-500 text-white hover:bg-amber-600'
                }`}
                onClick={() => onArmComposer(activeRequest.id)}
                disabled={disabled || busyRequestId === activeRequest.id}
              >
                {isArmed ? 'Custom answer active' : 'Write a custom answer'}
              </button>
              {isArmed && onDisarmComposer ? (
                <button
                  type="button"
                  className="rounded-full bg-white/85 px-3 py-1.5 text-xs font-semibold text-slate-700 transition hover:bg-white"
                  onClick={onDisarmComposer}
                  disabled={disabled}
                >
                  Send normal message instead
                </button>
              ) : null}
            </div>
          </div>
        </div>
      )}
    </section>
  )
}
