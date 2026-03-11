import { useState } from 'react'

import { ChevronLeft, ChevronRight, CircleHelp, MessageSquareQuote } from 'lucide-react'
import { Button } from 'react-aria-components'

import type { PendingHumanInputRequest } from '../../types/agentChat'

type HumanInputComposerPanelProps = {
  requests: PendingHumanInputRequest[]
  activeRequestId: string | null
  disabled?: boolean
  busyRequestId?: string | null
  onActiveRequestChange: (requestId: string) => void
  onSelectOption: (requestId: string, optionKey: string) => Promise<void> | void
}

type OptionDescriptionButtonProps = {
  optionTitle: string
  description: string
  disabled?: boolean
}

function OptionDescriptionButton({
  optionTitle,
  description,
  disabled = false,
}: OptionDescriptionButtonProps) {
  const [isPinnedOpen, setIsPinnedOpen] = useState(false)

  return (
    <div
      className="group/tooltip relative shrink-0"
      onMouseLeave={() => setIsPinnedOpen(false)}
    >
      <Button
        aria-label={`More information about ${optionTitle}`}
        aria-expanded={isPinnedOpen}
        className="inline-flex h-7 w-7 items-center justify-center rounded-full text-slate-400 transition hover:bg-white hover:text-slate-600 focus:bg-white focus:text-slate-600"
        isDisabled={disabled}
        onPress={() => setIsPinnedOpen((isOpen) => !isOpen)}
        onBlur={() => setIsPinnedOpen(false)}
      >
        <CircleHelp className="h-4 w-4" aria-hidden="true" />
      </Button>
      <div
        role="tooltip"
        className={`pointer-events-none absolute right-0 top-full z-50 mt-2 w-80 max-w-[min(24rem,calc(100vw-2rem))] rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm leading-5 text-slate-700 shadow-xl transition ${
          isPinnedOpen
            ? 'visible opacity-100'
            : 'invisible opacity-0 group-hover/tooltip:visible group-hover/tooltip:opacity-100 group-focus-within/tooltip:visible group-focus-within/tooltip:opacity-100'
        }`}
      >
        {description}
      </div>
    </div>
  )
}

export function HumanInputComposerPanel({
  requests,
  activeRequestId,
  disabled = false,
  busyRequestId = null,
  onActiveRequestChange,
  onSelectOption,
}: HumanInputComposerPanelProps) {
  if (!requests.length) {
    return null
  }

  const activeRequest = requests.find((request) => request.id === activeRequestId) ?? requests[0]
  const activeIndex = Math.max(0, requests.findIndex((request) => request.id === activeRequest.id))
  const isFreeTextOnly = activeRequest.inputMode === 'free_text_only' || activeRequest.options.length === 0

  return (
    <section
      className="bg-white px-4 py-4 text-slate-800"
      aria-label="Pending human input request"
    >
      <div className="flex items-start justify-between gap-4">
        <p className="min-w-0 flex-1 whitespace-pre-line text-[1.02rem] font-semibold leading-7 tracking-[-0.02em] text-slate-900">
          {activeRequest.question}
        </p>
        {requests.length > 1 ? (
          <div className="flex shrink-0 items-center gap-2 text-sm text-slate-500">
            <button
              type="button"
              className="inline-flex h-9 w-9 items-center justify-center rounded-xl border border-slate-200 bg-white text-slate-600 transition hover:border-slate-300 hover:bg-slate-50 hover:text-slate-900 disabled:cursor-not-allowed disabled:opacity-35"
              onClick={() => onActiveRequestChange(requests[Math.max(0, activeIndex - 1)].id)}
              disabled={disabled || activeIndex === 0}
              aria-label="Previous question"
            >
              <ChevronLeft className="h-4 w-4" aria-hidden="true" />
            </button>
            <span className="min-w-[3.5rem] text-center text-xs font-medium uppercase tracking-[0.16em] text-slate-400">
              {activeIndex + 1} of {requests.length}
            </span>
            <button
              type="button"
              className="inline-flex h-9 w-9 items-center justify-center rounded-xl border border-slate-200 bg-white text-slate-600 transition hover:border-slate-300 hover:bg-slate-50 hover:text-slate-900 disabled:cursor-not-allowed disabled:opacity-35"
              onClick={() => onActiveRequestChange(requests[Math.min(requests.length - 1, activeIndex + 1)].id)}
              disabled={disabled || activeIndex >= requests.length - 1}
              aria-label="Next question"
            >
              <ChevronRight className="h-4 w-4" aria-hidden="true" />
            </button>
          </div>
        ) : null}
      </div>

      {isFreeTextOnly ? (
        <div className="mt-4 flex items-center gap-3 border border-slate-200 bg-slate-50 px-4 py-3 text-sm leading-6 text-slate-600">
          <MessageSquareQuote className="h-4 w-4 shrink-0 text-slate-500" aria-hidden="true" />
          <div>
            <div className="font-semibold text-slate-900">Reply in the input below</div>
            <p className="mt-0.5">Use the composer below to reply.</p>
          </div>
        </div>
      ) : (
        <div className="mt-4 grid gap-2">
          {activeRequest.options.map((option, index) => {
            const isBusy = busyRequestId === activeRequest.id
            return (
              <div
                key={option.key}
                className="flex items-center gap-2 rounded-[0.9rem] border border-slate-200 bg-slate-50 px-2 py-2 transition hover:border-slate-300 hover:bg-slate-100"
              >
                <button
                  type="button"
                  onClick={() => void onSelectOption(activeRequest.id, option.key)}
                  disabled={disabled || isBusy}
                  className="group flex min-w-0 flex-1 items-center gap-3 rounded-[0.75rem] px-1 py-0.5 text-left disabled:cursor-wait disabled:opacity-60"
                >
                  <span className="w-5 shrink-0 text-sm font-semibold text-slate-400">
                    {index + 1}.
                  </span>
                  <div className="min-w-0 flex-1 text-sm font-semibold text-slate-900">
                    {option.title}
                  </div>
                </button>
                <OptionDescriptionButton
                  optionTitle={option.title}
                  description={option.description}
                  disabled={disabled}
                />
              </div>
            )
          })}
        </div>
      )}
    </section>
  )
}
