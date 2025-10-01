import type { ReactNode, Ref } from 'react'
import '../../styles/agentChatLegacy.css'
import { AgentComposer } from './AgentComposer'
import { ProcessingIndicator } from './ProcessingIndicator'
import { TimelineEventList } from './TimelineEventList'
import type { AgentTimelineProps } from './types'

type AgentChatLayoutProps = AgentTimelineProps & {
  agentName: string
  header?: ReactNode
  footer?: ReactNode
  onLoadOlder?: () => void
  onLoadNewer?: () => void
  onJumpToLatest?: () => void
  onSendMessage?: (body: string) => void | Promise<void>
  autoScrollPinned?: boolean
  hasUnseenActivity?: boolean
  timelineRef?: Ref<HTMLDivElement>
  bottomSentinelRef?: Ref<HTMLDivElement>
  loadingOlder?: boolean
  loadingNewer?: boolean
  initialLoading?: boolean
}

export function AgentChatLayout({
  agentName,
  agentFirstName,
  events,
  hasMoreOlder,
  hasMoreNewer,
  processingActive,
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
  const showProcessingIndicator = Boolean(processingActive && autoScrollPinned && !hasMoreNewer)
  const showBottomSentinel = !initialLoading && !hasMoreNewer
  const showLoadOlderButton = !initialLoading && (hasMoreOlder || loadingOlder)
  const showLoadNewerButton = !initialLoading && (hasMoreNewer || loadingNewer)

  const showJumpButton = hasMoreNewer || hasUnseenActivity || !autoScrollPinned

  return (
    <main className="min-h-screen">
      <div className="mx-auto flex min-h-screen w-full flex-col px-4 pb-0 sm:px-6 lg:px-10">
        <div
          id="agent-workspace-root"
          className="relative flex flex-1 flex-col gap-2"
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
                  initialLoading={initialLoading}
                />
              </div>

              <div id="processing-indicator-slot" className="processing-slot" data-visible={showProcessingIndicator ? 'true' : 'false'}>
                <ProcessingIndicator agentFirstName={agentFirstName} active={showProcessingIndicator} />
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

          <AgentComposer agentName={agentName} onSubmit={onSendMessage} />
        </div>
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
