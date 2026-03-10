import { MessageSquare, Rows3, type LucideIcon } from 'lucide-react'

export type SimplifiedChatViewOption = {
  key: 'conversational' | 'detail'
  enabled: boolean
  label: string
  description: string
  icon: LucideIcon
}

export function shouldShowStreamingThinking(args: {
  enabled: boolean
  isStreaming: boolean
  hasReasoning: boolean
  hasStreamingContent: boolean
  hasMoreNewer: boolean
}): boolean {
  return !args.enabled && args.isStreaming && args.hasReasoning && !args.hasStreamingContent && !args.hasMoreNewer
}

export function shouldUseTypingIndicator(enabled: boolean): boolean {
  return enabled
}

export function getSimplifiedChatViewOptions(): SimplifiedChatViewOption[] {
  return [
    {
      key: 'conversational',
      enabled: true,
      label: 'Conversational view',
      description: 'Focus on the back-and-forth conversation.',
      icon: MessageSquare,
    },
    {
      key: 'detail',
      enabled: false,
      label: 'Detail view',
      description: 'Show more step-by-step detail and updates.',
      icon: Rows3,
    },
  ]
}

export function getSimplifiedChatTriggerPresentation(enabled: boolean): {
  ariaLabel: string
  title: string
} {
  return {
    ariaLabel: enabled ? 'Change view. Conversational view is active.' : 'Change view. Detail view is active.',
    title: 'Change view',
  }
}
