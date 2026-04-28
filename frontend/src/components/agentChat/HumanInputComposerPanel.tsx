import type { KeyboardEvent } from 'react'
import { useEffect, useMemo, useRef } from 'react'
import { ArrowUp } from 'lucide-react'

import type { PendingHumanInputRequest } from '../../types/agentChat'
import { InlineInfoTooltipButton } from './InlineInfoTooltipButton'
import { orderHumanInputRequests } from './humanInputOrdering'

export const HUMAN_INPUT_OTHER_OPTION_KEY = '__other__'

type HumanInputDraftResponse = {
  selectedOptionKey?: string
  freeText?: string
}

type HumanInputComposerPanelProps = {
  requests: PendingHumanInputRequest[]
  agentName?: string | null
  activeRequestId: string | null
  draftResponses?: Record<string, HumanInputDraftResponse>
  disabled?: boolean
  busyRequestId?: string | null
  onSelectOption: (requestId: string, optionKey: string) => void
  onDraftFreeTextChange: (requestId: string, value: string) => void
  onSubmitRequest: () => Promise<void> | void
  onDismissRequest: (requestId: string) => Promise<void> | void
}

function isMacOS(): boolean {
  if (typeof navigator === 'undefined') return false
  return /Mac|iPod|iPhone|iPad/.test(navigator.platform)
}

function shouldShowSubmitShortcutHint(): boolean {
  if (typeof window === 'undefined') return true
  return window.innerWidth >= 768
}

function canSubmitRequest(
  request: PendingHumanInputRequest,
  draft: HumanInputDraftResponse | undefined,
): boolean {
  const trimmedFreeText = draft?.freeText?.trim() ?? ''
  if (request.inputMode === 'free_text_only' || request.options.length === 0) {
    return trimmedFreeText.length > 0
  }
  if (draft?.selectedOptionKey === HUMAN_INPUT_OTHER_OPTION_KEY) {
    return trimmedFreeText.length > 0
  }
  return Boolean(draft?.selectedOptionKey)
}

export function HumanInputComposerPanel({
  requests,
  agentName = null,
  activeRequestId,
  draftResponses = {},
  disabled = false,
  busyRequestId = null,
  onSelectOption,
  onDraftFreeTextChange,
  onSubmitRequest,
  onDismissRequest,
}: HumanInputComposerPanelProps) {
  const orderedRequests = useMemo(() => orderHumanInputRequests(requests), [requests])
  const activeRequest = orderedRequests.find((request) => request.id === activeRequestId) ?? orderedRequests[0] ?? null
  const activeDraft = activeRequest ? draftResponses[activeRequest.id] : undefined
  const isBusy = Boolean(activeRequest && busyRequestId === activeRequest.id)
  const otherInputRef = useRef<HTMLInputElement | null>(null)
  const standaloneTextareaRef = useRef<HTMLTextAreaElement | null>(null)

  const otherOptionTitle = `Other - tell ${agentName?.trim() || 'the agent'} what to do`
  const showStandaloneTextInput = Boolean(
    activeRequest
    && (
      activeRequest.inputMode === 'free_text_only'
      || activeRequest.options.length === 0
    )
  )
  const isOtherSelected = activeDraft?.selectedOptionKey === HUMAN_INPUT_OTHER_OPTION_KEY
  const canSubmit = activeRequest ? canSubmitRequest(activeRequest, activeDraft) : false
  const submitShortcutHint = shouldShowSubmitShortcutHint()
    ? `${isMacOS() ? '⌘↵' : 'Ctrl+↵'} to submit`
    : ''

  useEffect(() => {
    if ((!showStandaloneTextInput && !isOtherSelected) || disabled) {
      return
    }
    if (showStandaloneTextInput) {
      standaloneTextareaRef.current?.focus()
      return
    }
    otherInputRef.current?.focus()
  }, [activeRequest?.id, disabled, isOtherSelected, showStandaloneTextInput])

  if (!activeRequest) {
    return null
  }

  const activateOtherOption = () => {
    if (activeRequest.options.length === 0 || isOtherSelected) {
      return
    }
    onSelectOption(activeRequest.id, HUMAN_INPUT_OTHER_OPTION_KEY)
  }

  const handleTextKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== 'Enter' || event.nativeEvent.isComposing) {
      return
    }
    const shouldSubmit = (event.metaKey || event.ctrlKey) && !event.shiftKey && !event.altKey
    if (!shouldSubmit || !canSubmit || disabled || isBusy) {
      return
    }
    event.preventDefault()
    void onSubmitRequest()
  }

  const handleOtherInputKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key !== 'Enter' || event.nativeEvent.isComposing) {
      return
    }
    if (!canSubmit || disabled || isBusy) {
      return
    }
    event.preventDefault()
    void onSubmitRequest()
  }

  return (
    <section className="text-slate-800" aria-label="Pending human input request">
      <div className="overflow-hidden rounded-xl border border-slate-200 bg-white">
        {activeRequest.options.length > 0 ? (
          <>
            {activeRequest.options.map((option, index) => {
              const isSelected = activeDraft?.selectedOptionKey === option.key
              return (
                <div
                  key={option.key}
                  className={`relative flex items-center gap-1.5 border-b border-slate-200 px-1.5 py-1.5 transition ${
                    isSelected
                      ? 'bg-sky-50'
                      : 'bg-white hover:bg-slate-50'
                  }`}
                >
                  <button
                    type="button"
                    onClick={() => onSelectOption(activeRequest.id, option.key)}
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
                    disabled={disabled || isBusy}
                  />
                </div>
              )
            })}

            <div
              className={`relative flex items-center gap-2.5 px-1.5 py-1.5 transition ${
                isOtherSelected
                  ? 'bg-sky-50'
                  : 'bg-white hover:bg-slate-50'
              }`}
            >
              <div className="flex min-w-0 flex-1 items-center gap-2.5 rounded-md px-0.5 py-0.5">
                <span className={`w-4 shrink-0 text-xs font-semibold ${
                  isOtherSelected ? 'text-sky-600' : 'text-slate-400'
                }`}>
                  {activeRequest.options.length + 1}.
                </span>
                <input
                  ref={otherInputRef}
                  type="text"
                  value={activeDraft?.freeText ?? ''}
                  onFocus={activateOtherOption}
                  onChange={(event) => onDraftFreeTextChange(activeRequest.id, event.target.value)}
                  onKeyDown={handleOtherInputKeyDown}
                  disabled={disabled || isBusy}
                  placeholder={otherOptionTitle}
                  aria-label={otherOptionTitle}
                  className={`block h-5 min-w-0 flex-1 border-0 bg-transparent px-0 py-0 text-[13px] font-medium leading-5 tracking-[-0.01em] placeholder:text-slate-500/80 focus:outline-none focus:ring-0 disabled:cursor-wait disabled:opacity-60 ${
                    isOtherSelected
                      ? 'text-slate-900'
                      : 'text-slate-700'
                  }`}
                />
              </div>
            </div>
          </>
        ) : null}

        {showStandaloneTextInput ? (
          <div className="bg-white px-3 py-3">
            <textarea
              ref={standaloneTextareaRef}
              rows={3}
              value={activeDraft?.freeText ?? ''}
              onChange={(event) => onDraftFreeTextChange(activeRequest.id, event.target.value)}
              onKeyDown={handleTextKeyDown}
              disabled={disabled || isBusy}
              placeholder="Type your answer"
              className="block min-h-[6rem] w-full resize-none rounded-2xl border border-slate-200/80 bg-white px-3.5 py-3 text-[0.9375rem] leading-relaxed tracking-[-0.01em] text-slate-800 placeholder:text-slate-400/80 focus:border-sky-300 focus:outline-none focus:ring-2 focus:ring-sky-100 disabled:cursor-wait disabled:opacity-60"
            />
          </div>
        ) : null}
      </div>

      <div className="mt-3 flex items-center justify-between gap-3">
        <button
          type="button"
          onClick={() => void onDismissRequest(activeRequest.id)}
          disabled={disabled || isBusy}
          className="text-sm font-medium text-slate-500 transition hover:text-slate-900 disabled:cursor-wait disabled:opacity-50"
        >
          Dismiss
        </button>
        <div className="flex items-center gap-3">
          {submitShortcutHint ? (
            <span className="text-xs font-medium text-slate-400">{submitShortcutHint}</span>
          ) : null}
          <button
            type="button"
            className="composer-send-button"
            disabled={disabled || isBusy || !canSubmit}
            title={isBusy ? 'Submitting' : `Submit (${isMacOS() ? '⌘↵' : 'Ctrl+Enter'})`}
            aria-label={isBusy ? 'Submitting response' : 'Submit response'}
            onClick={() => void onSubmitRequest()}
          >
            {isBusy ? (
              <span className="inline-flex items-center justify-center">
                <span
                  className="h-4 w-4 animate-spin rounded-full border-2 border-white/60 border-t-white"
                  aria-hidden="true"
                />
                <span className="sr-only">Submitting</span>
              </span>
            ) : (
              <>
                <ArrowUp className="h-4 w-4" aria-hidden="true" />
                <span className="sr-only">Submit</span>
              </>
            )}
          </button>
        </div>
      </div>
    </section>
  )
}
