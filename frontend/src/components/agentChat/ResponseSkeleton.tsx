type ResponseSkeletonProps = {
  agentFirstName: string
}

export function ResponseSkeleton({ agentFirstName }: ResponseSkeletonProps) {
  return (
    <article className="timeline-event chat-event is-agent response-skeleton-event">
      <div className="chat-bubble chat-bubble--agent response-skeleton-bubble">
        <div className="chat-author chat-author--agent">
          {agentFirstName || 'Agent'}
        </div>
        <div className="response-skeleton-content">
          <div className="response-skeleton-line" style={{ width: '92%' }} />
          <div className="response-skeleton-line" style={{ width: '78%' }} />
          <div className="response-skeleton-line" style={{ width: '85%' }} />
          <div className="response-skeleton-line" style={{ width: '45%' }} />
        </div>
      </div>
    </article>
  )
}
