import { memo } from 'react'
import { ChevronDown, ChevronRight, EyeOff, FileText, Lightbulb, LightbulbOff, Link2, ListChecks } from 'lucide-react'
import { Button, Menu, MenuItem, MenuTrigger, Popover, type Key } from 'react-aria-components'
import { AgentChatSectionCard } from './uiPrimitives'

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

  const handleDisplayAction = (key: Key) => {
    if (key === 'hide-for-now') {
      onDismiss?.()
    } else if (key === 'turn-off-suggestions') {
      onTurnOff?.()
    }
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
          <MenuTrigger>
            <Button
              className="agent-chat-button starter-prompts-card__action"
              data-tone="neutral"
              data-variant="soft"
              data-size="sm"
            >
              <EyeOff size={13} aria-hidden="true" />
              <span>Hide</span>
              <ChevronDown size={11} aria-hidden="true" />
            </Button>
            <Popover
              className="agent-chat-menu-popover starter-prompts-card__menu-popover"
              placement="bottom end"
              offset={5}
            >
              <Menu
                aria-label="Suggestion display options"
                className="agent-chat-menu"
                onAction={handleDisplayAction}
              >
                {onDismiss ? (
                  <MenuItem
                    id="hide-for-now"
                    textValue="Hide for now"
                    className="agent-chat-menu-item starter-prompts-card__menu-item"
                  >
                    <EyeOff size={15} className="starter-prompts-card__menu-item-icon" aria-hidden="true" />
                    <span className="agent-chat-menu-item__copy">
                      <span className="agent-chat-menu-item__title">Hide for now</span>
                      <span className="agent-chat-menu-item__description">
                        Suggestions return after the agent&apos;s next update.
                      </span>
                    </span>
                  </MenuItem>
                ) : null}
                {onTurnOff ? (
                  <MenuItem
                    id="turn-off-suggestions"
                    textValue="Turn off suggestions"
                    className="agent-chat-menu-item starter-prompts-card__menu-item"
                  >
                    <LightbulbOff size={15} className="starter-prompts-card__menu-item-icon" aria-hidden="true" />
                    <span className="agent-chat-menu-item__copy">
                      <span className="agent-chat-menu-item__title">Turn off suggestions</span>
                      <span className="agent-chat-menu-item__description">
                        Re-enable them anytime in Settings.
                      </span>
                    </span>
                  </MenuItem>
                ) : null}
              </Menu>
            </Popover>
          </MenuTrigger>
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
