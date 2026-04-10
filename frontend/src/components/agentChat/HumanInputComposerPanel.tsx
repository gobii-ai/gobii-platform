import { ChevronLeft, ChevronRight, MessageSquareQuote } from 'lucide-react'

import type { PendingHumanInputRequest } from '../../types/agentChat'
import { InlineInfoTooltipButton } from './InlineInfoTooltipButton'

type HumanInputComposerPanelProps = {
  requests: PendingHumanInputRequest[]
  activeRequestId: string | null
  draftResponses?: Record<string, { selectedOptionKey?: string; freeText?: string }>
  disabled?: boolean
  busyRequestId?: string | null
  showQuestion?: boolean
  onActiveRequestChange: (requestId: string) => void
  onSelectOption: (requestId: string, optionKey: string) => Promise<void> | void
}

export function HumanInputComposerPanel({
  requests,
  activeRequestId,
  draftResponses = {},
  disabled = false,
  busyRequestId = null,
  showQuestion = true,
  onActiveRequestChange,
  onSelectOption,
}: HumanInputComposerPanelProps) {
  if (!requests.length) {
    return null
  }

  const batchOrder = new Map<string, number>()
  requests.forEach((request, index) => {
    if (!batchOrder.has(request.batchId)) {
      batchOrder.set(request.batchId, index)
    }
  })

  const orderedRequests = [...requests].sort((left, right) => {
    const leftBatchOrder = batchOrder.get(left.batchId) ?? 0
    const rightBatchOrder = batchOrder.get(right.batchId) ?? 0
    if (leftBatchOrder !== rightBatchOrder) {
      return leftBatchOrder - rightBatchOrder
    }
    return left.batchPosition - right.batchPosition
  })

  const activeRequest = orderedRequests.find((request) => request.id === activeRequestId) ?? orderedRequests[0]
  const activeIndex = Math.max(0, orderedRequests.findIndex((request) => request.id === activeRequest.id))
  const isFreeTextOnly = activeRequest.inputMode === 'free_text_only' || activeRequest.options.length === 0
  const activeDraft = draftResponses[activeRequest.id]

  return (
    <section
      className="bg-white px-3 py-3 text-slate-800"
      aria-label="Pending human input request"
    >
      {showQuestion || orderedRequests.length > 1 ? (
        <div className="flex items-start justify-between gap-3">
          {showQuestion ? (
            <p className="min-w-0 flex-1 whitespace-pre-line text-[0.95rem] font-semibold leading-6 tracking-[-0.02em] text-slate-900">
              {activeRequest.question}
            </p>
          ) : <div className="min-w-0 flex-1" />}
          {orderedRequests.length > 1 ? (
            <div className="flex shrink-0 items-center gap-1.5 text-sm text-slate-500">
              <button
                type="button"
                className="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-600 transition hover:border-slate-300 hover:bg-slate-50 hover:text-slate-900 disabled:cursor-not-allowed disabled:opacity-35"
                onClick={() => onActiveRequestChange(orderedRequests[Math.max(0, activeIndex - 1)].id)}
                disabled={disabled || activeIndex === 0}
                aria-label="Previous question"
              >
                <ChevronLeft className="h-4 w-4" aria-hidden="true" />
              </button>
              <span className="min-w-[3.25rem] text-center text-[10px] font-medium uppercase tracking-[0.14em] text-slate-400">
                {activeIndex + 1} of {orderedRequests.length}
              </span>
              <button
                type="button"
                className="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-600 transition hover:border-slate-300 hover:bg-slate-50 hover:text-slate-900 disabled:cursor-not-allowed disabled:opacity-35"
                onClick={() => onActiveRequestChange(orderedRequests[Math.min(orderedRequests.length - 1, activeIndex + 1)].id)}
                disabled={disabled || activeIndex >= orderedRequests.length - 1}
                aria-label="Next question"
              >
                <ChevronRight className="h-4 w-4" aria-hidden="true" />
              </button>
            </div>
          ) : null}
        </div>
      ) : null}

      {isFreeTextOnly ? (
        <div className={`${showQuestion || orderedRequests.length > 1 ? 'mt-3 ' : ''}flex items-center gap-2.5 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2.5 text-xs leading-5 text-slate-600`}>
          <MessageSquareQuote className="h-3.5 w-3.5 shrink-0 text-slate-500" aria-hidden="true" />
          <div>
            <div className="font-semibold text-slate-900">Reply in the input below</div>
            <p className="mt-0.5">Use the composer below to reply.</p>
          </div>
        </div>
      ) : (
        <div className={`${showQuestion || orderedRequests.length > 1 ? 'mt-3 ' : ''}rounded-xl border border-slate-200 bg-slate-50`}>
          {activeRequest.options.map((option, index) => {
            const isBusy = busyRequestId === activeRequest.id
            const isSelected = activeDraft?.selectedOptionKey === option.key
            return (
              <div
                key={option.key}
                className={`relative flex items-center gap-1.5 border-b border-slate-200 px-1.5 py-1.5 transition last:border-b-0 ${
                  isSelected
                    ? 'border-sky-300 bg-sky-50'
                    : 'bg-slate-50 hover:bg-slate-100'
                }`}
              >
                <button
                  type="button"
                  onClick={() => void onSelectOption(activeRequest.id, option.key)}
                  disabled={disabled || isBusy}
                  className={`group flex min-w-0 flex-1 items-center gap-2.5 rounded-md px-0.5 py-0.5 text-left disabled:cursor-wait disabled:opacity-60 ${
                    isSelected ? 'text-sky-950' : ''
                  }`}
                >
                  <span className={`w-4 shrink-0 text-xs font-semibold ${isSelected ? 'text-sky-600' : 'text-slate-400'}`}>
                    {index + 1}.
                  </span>
                  <div className={`min-w-0 flex-1 text-[13px] font-semibold leading-5 ${isSelected ? 'text-sky-950' : 'text-slate-900'}`}>
                    {option.title}
                  </div>
                </button>
                <InlineInfoTooltipButton
                  label={`More information about ${option.title}`}
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
