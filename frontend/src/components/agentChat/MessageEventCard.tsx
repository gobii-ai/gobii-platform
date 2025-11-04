import type { AgentMessage } from './types'
import { MessageContent } from './MessageContent'
import { formatRelativeTimestamp } from '../../util/time'
import { buildUserChatPalette } from '../../util/color'

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
  agentColorHex?: string
}

export function MessageEventCard({ eventCursor, message, agentFirstName, agentColorHex }: MessageEventCardProps) {
  const isAgent = Boolean(message.isOutbound)
  const channel = (message.channel || 'web').toLowerCase()
  const hasPeerMetadata = Boolean(message.peerAgent || message.peerLinkId)
  const isPeer = Boolean(message.isPeer || hasPeerMetadata || channel === 'other')

  const selfName = message.selfAgentName || agentFirstName || 'Agent'
  const peerName = message.peerAgent?.name || 'Linked agent'
  const peerDirectionLabel = message.isOutbound ? `${selfName} → ${peerName}` : `${peerName} → ${selfName}`

  const bubbleTheme = isPeer
    ? message.isOutbound
      ? 'chat-bubble--peer-out'
      : 'chat-bubble--peer-in'
    : isAgent
      ? 'chat-bubble--agent'
      : 'chat-bubble--user'

  const authorTheme = isPeer
    ? 'chat-author--peer'
    : isAgent
      ? 'chat-author--agent'
      : 'chat-author--user'

  let authorLabel = isAgent ? agentFirstName || 'Agent' : 'You'
  if (isPeer) {
    authorLabel = peerDirectionLabel
  }

  const metaTheme = isPeer ? 'chat-meta chat-meta--peer' : isAgent ? 'chat-meta' : 'chat-meta is-user'

  let channelLabel = getChannelLabel(channel)
  let showChannelTag = channel !== 'web'
  if (isPeer) {
    channelLabel = 'Peer DM'
    showChannelTag = true
  }

  const relativeLabel = message.relativeTimestamp || formatRelativeTimestamp(message.timestamp) || ''
  const palette = !isPeer && !isAgent ? buildUserChatPalette(agentColorHex) : null

  const contentTone = isPeer ? 'text-slate-800' : isAgent ? 'text-slate-800' : ''

  const alignmentClass = isPeer ? 'is-agent' : isAgent ? 'is-agent' : 'is-user'

  const channelTagBaseClass = 'ml-2 inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide'
  const channelTagClass = isPeer
    ? `${channelTagBaseClass} border border-indigo-200 bg-indigo-50 text-indigo-600`
    : isAgent
      ? `${channelTagBaseClass} border border-indigo-100 bg-indigo-50 text-indigo-600`
      : `${channelTagBaseClass} user-channel-badge`

  const bubbleStyle = palette?.cssVars

  return (
    <article className={`timeline-event chat-event ${alignmentClass} ${isPeer ? 'is-peer' : ''}`} data-cursor={eventCursor}>
      <div className={`chat-bubble ${bubbleTheme}`} style={bubbleStyle}>
        <div className={`chat-author ${authorTheme}`}>
          {authorLabel}
          {showChannelTag ? <span className={channelTagClass}>{channelLabel}</span> : null}
        </div>
        <div
          className={`chat-content prose prose-sm max-w-none leading-relaxed ${contentTone}`}
        >
          <MessageContent bodyHtml={message.bodyHtml} bodyText={message.bodyText} />
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
