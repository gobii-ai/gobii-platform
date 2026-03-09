import { memo } from 'react'
import { ChevronRight, FileText, Lightbulb, Link2, ListChecks } from 'lucide-react'

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
  variant?: 'chips' | 'timeline-list'
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
  variant = 'chips',
  onSelect,
}: StarterPromptSuggestionsProps) {
  if (!loading && !prompts.length) {
    return null
  }

  if (variant === 'timeline-list') {
    return (
      <section
        className="timeline-event starter-prompts-card"
        aria-label="Suggested follow-ups"
        aria-busy={loading}
      >
        <h3 className="starter-prompts-card__title">Suggested follow-ups</h3>
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
                    className={`starter-prompts-card__text-pulse starter-prompts-card__text-pulse--${index % 3}`}
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
      </section>
    )
  }

  return (
    <div className="flex flex-wrap gap-1.5" aria-label="Starter prompts">
      {prompts.map((prompt, index) => (
        <button
          key={prompt.id}
          type="button"
          disabled={disabled}
          onClick={() => {
            void onSelect?.(prompt, index)
          }}
          className="inline-flex rounded-full bg-slate-100 px-2.5 py-1 text-left text-xs font-medium leading-5 text-slate-700 transition hover:bg-slate-200 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {prompt.text}
        </button>
      ))}
    </div>
  )
})
