import type { AgentMessage } from './types'
import { formatRelativeTimestamp } from '../../util/time'

const CHANNEL_LABELS: Record<string, string> = {
  email: 'Email',
  sms: 'SMS',
  slack: 'Slack',
  discord: 'Discord',
  web: 'Web',
  other: 'Other',
}

function getChannelLabel(raw?: string) {
  if (!raw) return 'Other'
  const normalized = raw.toLowerCase()
  return CHANNEL_LABELS[normalized] || raw.replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase())
}

type MessageEventCardProps = {
  eventCursor: string
  message: AgentMessage
  agentFirstName: string
}

export function MessageEventCard({ eventCursor, message, agentFirstName }: MessageEventCardProps) {
  const isAgent = Boolean(message.isOutbound)
  const bubbleTheme = isAgent ? 'chat-bubble--agent' : 'chat-bubble--user'
  const authorTheme = isAgent ? 'chat-author--agent' : 'chat-author--user'
  const metaTheme = isAgent ? 'chat-meta' : 'chat-meta is-user'
  const authorLabel = isAgent ? agentFirstName || 'Agent' : 'You'
  const channel = message.channel || 'web'
  const channelLabel = getChannelLabel(channel)
  const showChannelTag = channel.toLowerCase() !== 'web'
  const hasHtml = Boolean(message.bodyHtml)
  const relativeLabel = message.relativeTimestamp || formatRelativeTimestamp(message.timestamp) || ''

  return (
    <article className={`timeline-event chat-event ${isAgent ? 'is-agent' : 'is-user'}`} data-cursor={eventCursor}>
      <div className={`chat-bubble ${bubbleTheme}`}>
        <div className={`chat-author ${authorTheme}`}>
          {authorLabel}
          {showChannelTag ? (
            <span
              className={`ml-2 inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${
                isAgent
                  ? 'border border-indigo-100 bg-indigo-50 text-indigo-600'
                  : 'border border-white/40 bg-white/10 text-white/80'
              }`}
            >
              {channelLabel}
            </span>
          ) : null}
        </div>
        <div className={`chat-content prose prose-sm max-w-none leading-relaxed ${isAgent ? 'text-slate-800' : 'text-white'}`}>
          {hasHtml ? (
            <div dangerouslySetInnerHTML={{ __html: message.bodyHtml || '' }} />
          ) : (
            <p>{message.bodyText}</p>
          )}
        </div>
        {message.attachments && message.attachments.length > 0 ? (
          <div className="chat-attachments">
            {message.attachments.map((attachment) => (
              <a key={attachment.id} href={attachment.url} target="_blank" rel="noopener noreferrer">
                <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.415-6.414a4 4 0 10-5.657-5.657l-6.415 6.414" />
                </svg>
                <span className="truncate max-w-[160px]" title={attachment.filename}>
                  {attachment.filename}
                </span>
                {attachment.fileSizeLabel ? (
                  <span className={isAgent ? 'text-slate-500' : ''}>{attachment.fileSizeLabel}</span>
                ) : null}
              </a>
            ))}
          </div>
        ) : null}
      </div>
      <div className={metaTheme} title={message.timestamp || undefined}>
        {relativeLabel || message.timestamp}
      </div>
    </article>
  )
}
