import type { ReactNode, Ref } from 'react'
import '../../styles/agentChatLegacy.css'
import { AgentComposer } from './AgentComposer'
import { ProcessingIndicator } from './ProcessingIndicator'
import { TimelineEventList } from './TimelineEventList'
import { ThinkingBubble } from './ThinkingBubble'
import { StreamingReplyCard } from './StreamingReplyCard'
import type { AgentTimelineProps } from './types'
import type { ProcessingWebTask, StreamState } from '../../types/agentChat'
import type { CompletedThinking } from '../../stores/agentChatStore'
import { buildAgentComposerPalette } from '../../util/color'

type AgentChatLayoutProps = AgentTimelineProps & {
  agentColorHex?: string | null
  header?: ReactNode
  footer?: ReactNode
  onLoadOlder?: () => void
  onLoadNewer?: () => void
  onJumpToLatest?: () => void
  onSendMessage?: (body: string, attachments?: File[]) => void | Promise<void>
  autoScrollPinned?: boolean
  hasUnseenActivity?: boolean
  timelineRef?: Ref<HTMLDivElement>
  bottomSentinelRef?: Ref<HTMLDivElement>
  loadingOlder?: boolean
  loadingNewer?: boolean
  initialLoading?: boolean
  processingWebTasks?: ProcessingWebTask[]
  streaming?: StreamState | null
  thinkingCollapsed?: boolean
  completedThinking?: CompletedThinking | null
  onToggleThinking?: () => void
}

export function AgentChatLayout({
  agentFirstName,
  events,
  agentColorHex,
  header,
  footer,
  hasMoreOlder,
  hasMoreNewer,
  processingActive,
  processingWebTasks = [],
  streaming,
  thinkingCollapsed = false,
  completedThinking,
  onToggleThinking,
  onLoadOlder,
  onLoadNewer,
  onJumpToLatest,
  onSendMessage,
  autoScrollPinned = true,
  hasUnseenActivity = false,
  timelineRef,
  bottomSentinelRef,
  loadingOlder = false,
  loadingNewer = false,
  initialLoading = false,
}: AgentChatLayoutProps) {
  const isStreaming = Boolean(streaming && !streaming.done)
  const hasStreamingReasoning = Boolean(streaming?.reasoning?.trim())
  const hasCompletedReasoning = Boolean(completedThinking?.reasoning?.trim())
  const hasStreamingContent = Boolean(streaming?.content?.trim())
  const showStreamingReasoning = Boolean(isStreaming && hasStreamingReasoning)

  const showProcessingIndicator = Boolean((processingActive || isStreaming) && !hasMoreNewer)
  const showBottomSentinel = !initialLoading && !hasMoreNewer
  const showLoadOlderButton = !initialLoading && (hasMoreOlder || loadingOlder)
  const showLoadNewerButton = !initialLoading && (hasMoreNewer || loadingNewer)

  const showJumpButton = hasMoreNewer || hasUnseenActivity || !autoScrollPinned

  const containerStyle = header
    ? { paddingTop: 'calc(var(--agent-chat-banner-height, 0px) + 1.5rem)' }
    : undefined
  const composerPalette = buildAgentComposerPalette(agentColorHex)

  return (
    <main className="min-h-screen">
      <div className="mx-auto flex min-h-screen w-full flex-col px-4 pb-0 pt-6 sm:px-6 lg:px-10" style={containerStyle}>
        {header ? <div className="relative z-30">{header}</div> : null}
        <div
          id="agent-workspace-root"
          className="relative flex flex-1 flex-col gap-2"
          style={composerPalette.cssVars}
        >
          <div id="timeline-shell" className="relative flex-1">
            <div ref={timelineRef} id="timeline-events" className="flex flex-col gap-3" data-has-jump-button={showJumpButton ? 'true' : 'false'}>
              <div
                id="timeline-load-older"
                className="timeline-load-control"
                data-side="older"
                data-state={loadingOlder ? 'loading' : hasMoreOlder ? 'has-more' : 'exhausted'}
                hidden={!showLoadOlderButton}
              >
                <button
                  type="button"
                  className="timeline-load-button"
                  hidden={!showLoadOlderButton}
                  onClick={onLoadOlder}
                  disabled={loadingOlder}
                >
                  <span className="timeline-load-indicator" data-loading={loadingOlder ? 'true' : 'false'} aria-hidden="true" />
                  <span className="timeline-load-label">{loadingOlder ? 'Loading…' : 'Load older'}</span>
                </button>
                <span className="timeline-history-label" hidden={hasMoreOlder || initialLoading}>
                  Beginning of history
                </span>
              </div>

              <div id="timeline-event-list" className="flex flex-col gap-3">
                <TimelineEventList
                  agentFirstName={agentFirstName}
                  events={events}
                  agentColorHex={agentColorHex || undefined}
                  initialLoading={initialLoading}
                  thinkingReasoning={hasCompletedReasoning && !isStreaming ? completedThinking?.reasoning : undefined}
                  thinkingCollapsed={thinkingCollapsed}
                  onToggleThinking={onToggleThinking}
                />
              </div>

              {(showStreamingReasoning || hasStreamingContent) && !hasMoreNewer ? (
                <div id="streaming-response-slot" className="streaming-response-slot flex flex-col gap-3">
                  {showStreamingReasoning && onToggleThinking ? (
                    <ThinkingBubble
                      reasoning={streaming?.reasoning || ''}
                      isStreaming={isStreaming}
                      collapsed={thinkingCollapsed}
                      onToggle={onToggleThinking}
                    />
                  ) : null}
                  {hasStreamingContent ? (
                    <StreamingReplyCard
                      content={streaming?.content || ''}
                      agentFirstName={agentFirstName}
                      isStreaming={isStreaming}
                    />
                  ) : null}
                </div>
              ) : null}

              <div id="processing-indicator-slot" className="processing-slot" data-visible={showProcessingIndicator ? 'true' : 'false'}>
                <ProcessingIndicator
                  agentFirstName={agentFirstName}
                  active={Boolean(processingActive)}
                  tasks={processingWebTasks}
                  isStreaming={isStreaming}
                />
              </div>

              {showBottomSentinel ? (
                <div ref={bottomSentinelRef} id="timeline-bottom-sentinel" className="timeline-bottom-sentinel" aria-hidden="true" />
              ) : null}

              <div
                id="timeline-load-newer"
                className="timeline-load-control"
                data-side="newer"
                data-state={loadingNewer ? 'loading' : hasMoreNewer ? 'has-more' : 'exhausted'}
                hidden={!showLoadNewerButton}
              >
                <button
                  type="button"
                  className="timeline-load-button"
                  hidden={!showLoadNewerButton}
                  onClick={onLoadNewer}
                  disabled={loadingNewer}
                >
                  <span className="timeline-load-indicator" data-loading={loadingNewer ? 'true' : 'false'} aria-hidden="true" />
                  <span className="timeline-load-label">{loadingNewer ? 'Loading…' : 'Load newer'}</span>
                </button>
              </div>
            </div>
          </div>

          <AgentComposer onSubmit={onSendMessage} />
        </div>
        {footer ? <div className="mt-6">{footer}</div> : null}
      </div>

      <button
        id="jump-to-latest"
        className="jump-to-latest"
        type="button"
        aria-label="Jump to latest"
        aria-hidden={showJumpButton ? 'false' : 'true'}
        onClick={onJumpToLatest}
        data-has-activity={hasUnseenActivity ? 'true' : 'false'}
        data-visible={showJumpButton ? 'true' : 'false'}
      >
        <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path strokeLinecap="round" strokeLinejoin="round" d="M12 5v14m0 0-5-5m5 5 5-5" />
        </svg>
        <span className="sr-only">Jump to latest</span>
      </button>
    </main>
  )
}
