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
  loadingOlder?: boolean
  loadingNewer?: boolean
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
  loadingOlder = false,
  loadingNewer = false,
}: AgentChatLayoutProps) {
  return (
    <main className="min-h-screen bg-slate-50">
      <div className="mx-auto flex min-h-screen w-full flex-col gap-6 px-4 pb-0 pt-6 sm:px-6 lg:px-10">
        <div
          id="agent-workspace-root"
          className="relative flex flex-1 flex-col gap-4"
        >
          <div id="timeline-shell" className="relative flex-1">
            <div ref={timelineRef} id="timeline-events" className="flex flex-col gap-3">
              <div
                id="timeline-load-older"
                className="timeline-load-control border-b border-slate-100"
                data-side="older"
                data-state={loadingOlder ? 'loading' : hasMoreOlder ? 'has-more' : 'exhausted'}
              >
                <button
                  type="button"
                  className="timeline-load-button"
                  hidden={!hasMoreOlder && !loadingOlder}
                  onClick={onLoadOlder}
                  disabled={loadingOlder}
                >
                  <span className="timeline-load-indicator" data-loading={loadingOlder ? 'true' : 'false'} aria-hidden="true" />
                  <span className="timeline-load-label">{loadingOlder ? 'Loading…' : 'Load older'}</span>
                </button>
                <span className="timeline-history-label" hidden={hasMoreOlder}>
                  Beginning of history
                </span>
              </div>

              <div id="timeline-event-list" className="flex flex-col gap-3">
                <TimelineEventList agentFirstName={agentFirstName} events={events} />
              </div>

              <div id="processing-indicator-slot" className="processing-slot" data-visible={processingActive ? 'true' : 'false'}>
                <ProcessingIndicator agentFirstName={agentFirstName} active={!!processingActive} />
              </div>

              <div
                id="timeline-load-newer"
                className="timeline-load-control border-t border-slate-100"
                data-side="newer"
                data-state={loadingNewer ? 'loading' : hasMoreNewer ? 'has-more' : 'exhausted'}
                hidden={!hasMoreNewer && !loadingNewer}
              >
                <button
                  type="button"
                  className="timeline-load-button"
                  hidden={!hasMoreNewer && !loadingNewer}
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
        className={`jump-to-latest ${autoScrollPinned && !hasMoreNewer ? '!hidden' : ''}`}
        type="button"
        aria-label="Jump to latest"
        aria-hidden={autoScrollPinned && !hasMoreNewer ? 'true' : 'false'}
        onClick={onJumpToLatest}
        data-has-activity={hasUnseenActivity ? 'true' : 'false'}
      >
        <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path strokeLinecap="round" strokeLinejoin="round" d="M12 5v14m0 0-5-5m5 5 5-5" />
        </svg>
        <span className="sr-only">Jump to latest</span>
      </button>
    </main>
  )
}
