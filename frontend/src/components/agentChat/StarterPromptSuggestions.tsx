import { memo } from 'react'
import { ChevronRight, EyeOff, FileText, Lightbulb, LightbulbOff, Link2, ListChecks } from 'lucide-react'
import { AgentChatButton, AgentChatSectionCard } from './uiPrimitives'

export type StarterPrompt = {
  id: string
  text: string
  category: 'capabilities' | 'deliverables' | 'integrations' | 'planning'
}

type StarterPromptSuggestionsProps = {
  prompts: StarterPrompt[]
  loading?: boolean
  loadingCount?: number
  disabled?: boolean
  onDismiss?: () => void
  onTurnOff?: () => void
  onSelect?: (prompt: StarterPrompt, position: number) => void | Promise<void>
}

function iconForPromptCategory(category: StarterPrompt['category']) {
  switch (category) {
    case 'deliverables':
      return FileText
    case 'integrations':
      return Link2
    case 'planning':
      return ListChecks
    default:
      return Lightbulb
  }
}

export const StarterPromptSuggestions = memo(function StarterPromptSuggestions({
  prompts,
  loading = false,
  loadingCount = 3,
  disabled = false,
  onDismiss,
  onTurnOff,
  onSelect,
}: StarterPromptSuggestionsProps) {
  if (!loading && !prompts.length) {
    return null
  }

  return (
    <AgentChatSectionCard
      className="timeline-event starter-prompts-card"
      tone="info"
      density="compact"
      aria-label="Suggested follow-ups"
      aria-busy={loading}
    >
      <div className="starter-prompts-card__header">
        <h3 className="starter-prompts-card__title">Suggested follow-ups</h3>
        {onDismiss || onTurnOff ? (
          <div className="starter-prompts-card__actions" role="group" aria-label="Suggestion display options">
            {onDismiss ? (
              <AgentChatButton
                className="starter-prompts-card__action"
                size="sm"
                onClick={onDismiss}
                title="Hide this set until the agent replies or finishes new work"
              >
                <EyeOff className="starter-prompts-card__action-icon" aria-hidden="true" />
                Hide for now
              </AgentChatButton>
            ) : null}
            {onTurnOff ? (
              <AgentChatButton
                className="starter-prompts-card__action"
                size="sm"
                onClick={onTurnOff}
                title="Hide future suggestions until you turn them back on in Settings"
              >
                <LightbulbOff className="starter-prompts-card__action-icon" aria-hidden="true" />
                Turn off suggestions
              </AgentChatButton>
            ) : null}
          </div>
        ) : null}
      </div>
      <div className="starter-prompts-card__rows" role="list">
        {loading
          ? Array.from({ length: Math.max(1, loadingCount) }).map((_, index) => (
            <div
              key={`suggestion-loading-${index + 1}`}
              className="starter-prompts-card__row starter-prompts-card__row--loading"
              aria-hidden="true"
            >
              <span className="starter-prompts-card__icon-wrap starter-prompts-card__icon-wrap--loading">
                <span className="starter-prompts-card__pulse-dot" />
              </span>
              <span className="starter-prompts-card__text starter-prompts-card__text--loading">
                <span
                  className="starter-prompts-card__text-pulse"
                  data-pulse-index={index % 3}
                />
              </span>
              <span className="starter-prompts-card__chevron starter-prompts-card__chevron--loading" />
            </div>
          ))
          : prompts.map((prompt, index) => {
            const Icon = iconForPromptCategory(prompt.category)
            return (
              <button
                key={prompt.id}
                type="button"
                disabled={disabled}
                onClick={() => {
                  void onSelect?.(prompt, index)
                }}
                className="starter-prompts-card__row"
                aria-label={`Suggested follow-up: ${prompt.text}`}
              >
                <span className="starter-prompts-card__icon-wrap" aria-hidden="true">
                  <Icon className="starter-prompts-card__icon" />
                </span>
                <span className="starter-prompts-card__text">{prompt.text}</span>
                <ChevronRight className="starter-prompts-card__chevron" aria-hidden="true" />
              </button>
            )
          })}
      </div>
      {loading ? <span className="sr-only">Loading suggested follow-ups</span> : null}
    </AgentChatSectionCard>
  )
})
