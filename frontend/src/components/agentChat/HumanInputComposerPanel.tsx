import { MessageSquareQuote } from 'lucide-react'

import type { PendingHumanInputRequest } from '../../types/agentChat'
import { InlineInfoTooltipButton } from './InlineInfoTooltipButton'

type HumanInputComposerPanelProps = {
  requests: PendingHumanInputRequest[]
  activeRequestId: string | null
  draftResponses?: Record<string, { selectedOptionKey?: string; freeText?: string }>
  disabled?: boolean
  busyRequestId?: string | null
  onSelectOption: (requestId: string, optionKey: string) => Promise<void> | void
}

export function HumanInputComposerPanel({
  requests,
  activeRequestId,
  draftResponses = {},
  disabled = false,
  busyRequestId = null,
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
  const isFreeTextOnly = activeRequest.inputMode === 'free_text_only' || activeRequest.options.length === 0
  const activeDraft = draftResponses[activeRequest.id]

  return (
    <section
      className="bg-white px-3 py-3 text-slate-800"
      aria-label="Pending human input request"
    >
      {isFreeTextOnly ? (
        <div className="flex items-center gap-2.5 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2.5 text-xs leading-5 text-slate-600">
          <MessageSquareQuote className="h-3.5 w-3.5 shrink-0 text-slate-500" aria-hidden="true" />
          <div>
            <div className="font-semibold text-slate-900">Reply in the input below</div>
            <p className="mt-0.5">Use the composer below to reply.</p>
          </div>
        </div>
      ) : (
        <div className="overflow-hidden rounded-xl border border-slate-200 bg-slate-50">
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
