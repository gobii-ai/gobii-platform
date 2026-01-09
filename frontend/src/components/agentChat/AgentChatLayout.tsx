import type { ReactNode, Ref } from 'react'
import { useState, useCallback } from 'react'
import '../../styles/agentChatLegacy.css'
import { AgentComposer } from './AgentComposer'
import { ProcessingIndicator } from './ProcessingIndicator'
import { TimelineEventList } from './TimelineEventList'
import { ThinkingBubble } from './ThinkingBubble'
import { StreamingReplyCard } from './StreamingReplyCard'
import { ChatSidebar } from './ChatSidebar'
import { AgentChatBanner, type ConnectionStatusTone } from './AgentChatBanner'
import { InsightEventCard } from './insights'
import type { AgentTimelineProps } from './types'
import type { ProcessingWebTask, StreamState, KanbanBoardSnapshot } from '../../types/agentChat'
import type { InsightEvent } from '../../types/insight'
import type { AgentRosterEntry } from '../../types/agentRoster'
import { buildAgentComposerPalette } from '../../util/color'

type AgentChatLayoutProps = AgentTimelineProps & {
  agentColorHex?: string | null
  agentAvatarUrl?: string | null
  agentName?: string | null
  connectionStatus?: ConnectionStatusTone
  connectionLabel?: string
  connectionDetail?: string | null
  agentRoster?: AgentRosterEntry[]
  activeAgentId?: string | null
  switchingAgentId?: string | null
  rosterLoading?: boolean
  rosterError?: string | null
  onSelectAgent?: (agent: AgentRosterEntry) => void
  kanbanSnapshot?: KanbanBoardSnapshot | null
  footer?: ReactNode
  onLoadOlder?: () => void
  onLoadNewer?: () => void
  onJumpToLatest?: () => void
  onClose?: () => void
  onSendMessage?: (body: string, attachments?: File[]) => void | Promise<void>
  autoScrollPinned?: boolean
  hasUnseenActivity?: boolean
  timelineRef?: Ref<HTMLDivElement>
  bottomSentinelRef?: Ref<HTMLDivElement>
  loadingOlder?: boolean
  loadingNewer?: boolean
  initialLoading?: boolean
  processingWebTasks?: ProcessingWebTask[]
  awaitingResponse?: boolean
  streaming?: StreamState | null
  thinkingCollapsedByCursor?: Record<string, boolean>
  onToggleThinking?: (cursor: string) => void
  streamingThinkingCollapsed?: boolean
  onToggleStreamingThinking?: () => void
  currentInsight?: InsightEvent | null
  onDismissInsight?: (insightId: string) => void
}

export function AgentChatLayout({
  agentFirstName,
  events,
  agentColorHex,
  agentAvatarUrl,
  agentName,
  connectionStatus,
  connectionLabel,
  connectionDetail,
  agentRoster,
  activeAgentId,
  switchingAgentId,
  rosterLoading,
  rosterError,
  onSelectAgent,
  kanbanSnapshot,
  footer,
  hasMoreOlder,
  hasMoreNewer,
  processingActive,
  awaitingResponse = false,
  processingWebTasks = [],
  streaming,
  thinkingCollapsedByCursor,
  onToggleThinking,
  streamingThinkingCollapsed = false,
  onToggleStreamingThinking,
  onLoadOlder,
  onLoadNewer,
  onJumpToLatest,
  onClose,
  onSendMessage,
  autoScrollPinned = true,
  hasUnseenActivity = false,
  timelineRef,
  bottomSentinelRef,
  loadingOlder = false,
  loadingNewer = false,
  initialLoading = false,
  currentInsight,
  onDismissInsight,
}: AgentChatLayoutProps) {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(true)

  const handleSidebarToggle = useCallback((collapsed: boolean) => {
    setSidebarCollapsed(collapsed)
  }, [])

  const isStreaming = Boolean(streaming && !streaming.done)
  const hasStreamingReasoning = Boolean(streaming?.reasoning?.trim())
  const hasStreamingContent = Boolean(streaming?.content?.trim())
  // Only show streaming reasoning while actually streaming - once done, the historical event takes over
  const showStreamingReasoning = hasStreamingReasoning && isStreaming

  const showProcessingIndicator = Boolean((processingActive || isStreaming || awaitingResponse) && !hasMoreNewer)
  const showBottomSentinel = !initialLoading && !hasMoreNewer
  const showLoadOlderButton = !initialLoading && (hasMoreOlder || loadingOlder)
  const showLoadNewerButton = !initialLoading && (hasMoreNewer || loadingNewer)

  const showJumpButton = hasMoreNewer || hasUnseenActivity || !autoScrollPinned

  const showBanner = Boolean(agentName)
  const containerStyle = showBanner
    ? { paddingTop: 'calc(var(--agent-chat-banner-height, 0px) + 1.5rem)' }
    : undefined
  const composerPalette = buildAgentComposerPalette(agentColorHex)

  const mainClassName = `has-sidebar ${sidebarCollapsed ? 'has-sidebar--collapsed' : ''}`

  return (
    <>
      <ChatSidebar
        defaultCollapsed={true}
        onToggle={handleSidebarToggle}
        agents={agentRoster}
        activeAgentId={activeAgentId}
        switchingAgentId={switchingAgentId}
        loading={rosterLoading}
        errorMessage={rosterError}
        onSelectAgent={onSelectAgent}
      />
      {showBanner && (
        <AgentChatBanner
          agentName={agentName || 'Agent'}
          agentAvatarUrl={agentAvatarUrl}
          agentColorHex={agentColorHex}
          connectionStatus={connectionStatus}
          connectionLabel={connectionLabel}
          connectionDetail={connectionDetail}
          kanbanSnapshot={kanbanSnapshot}
          processingActive={processingActive}
          onClose={onClose}
          sidebarCollapsed={sidebarCollapsed}
        />
      )}
      <main className={`min-h-screen ${mainClassName}`}>
        <div className="mx-auto flex min-h-screen w-full flex-col px-4 pb-0 pt-6 sm:px-6 lg:px-10" style={containerStyle}>
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
                    thinkingCollapsedByCursor={thinkingCollapsedByCursor}
                    onToggleThinking={onToggleThinking}
                  />
                </div>

                {(showStreamingReasoning || hasStreamingContent) && !hasMoreNewer ? (
                  <div id="streaming-response-slot" className="streaming-response-slot flex flex-col gap-3">
                    {showStreamingReasoning && onToggleStreamingThinking ? (
                      <ThinkingBubble
                        reasoning={streaming?.reasoning || ''}
                        isStreaming={isStreaming}
                        collapsed={streamingThinkingCollapsed}
                        onToggle={onToggleStreamingThinking}
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

                {/* Insight slot - shows during agent working state */}
                {showProcessingIndicator && currentInsight ? (
                  <div id="insight-slot" className="insight-slot">
                    <InsightEventCard
                      insight={currentInsight}
                      onDismiss={onDismissInsight}
                    />
                  </div>
                ) : null}

                <div id="processing-indicator-slot" className="processing-slot" data-visible={showProcessingIndicator ? 'true' : 'false'}>
                  <ProcessingIndicator
                    agentFirstName={agentFirstName}
                    active={Boolean(processingActive || awaitingResponse)}
                    tasks={processingWebTasks}
                    isStreaming={Boolean(isStreaming || awaitingResponse)}
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
    </>
  )
}
