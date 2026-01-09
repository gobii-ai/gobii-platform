type ResponseSkeletonProps = {
  agentFirstName: string
}

export function ResponseSkeleton({ agentFirstName }: ResponseSkeletonProps) {
  return (
    <article className="timeline-event chat-event is-agent response-skeleton-event">
      <div className="chat-bubble chat-bubble--agent response-skeleton-bubble">
        <div className="response-skeleton-row">
          <span className="chat-author chat-author--agent">
            {agentFirstName || 'Agent'}
          </span>
          <div className="response-skeleton-line" />
        </div>
      </div>
    </article>
  )
}
