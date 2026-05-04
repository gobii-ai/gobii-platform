import ReactJsonView from '@microlink/react-json-view'
import { memo } from 'react'
import type { AgentMessage } from './types'
import { MessageContent } from './MessageContent'
import { AgentAvatarBadge } from '../common/AgentAvatarBadge'
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
  agentAvatarUrl?: string | null
  viewerUserId?: number | null
  viewerEmail?: string | null
  onMessageLinkClick?: (href: string) => boolean | void
}

// Only animate messages that arrived recently (within last 3 seconds)
function isRecentMessage(timestamp?: string | null): boolean {
  if (!timestamp) return false
  const messageTime = Date.parse(timestamp)
  if (Number.isNaN(messageTime)) return false
  return Date.now() - messageTime < 3000
}

export const MessageEventCard = memo(function MessageEventCard({
  eventCursor,
  message,
  agentFirstName,
  agentColorHex,
  agentAvatarUrl,
  viewerUserId,
  viewerEmail,
  onMessageLinkClick,
}: MessageEventCardProps) {
  const isAgent = Boolean(message.isOutbound)
  const shouldAnimate = isAgent && isRecentMessage(message.timestamp)
  const channel = (message.channel || 'web').toLowerCase()
  const sourceKind = (message.sourceKind || '').toLowerCase()
  const isWebhook = sourceKind === 'webhook'
  const hasPeerMetadata = Boolean(message.peerAgent || message.peerLinkId)
  const isPeer = Boolean(message.isPeer || hasPeerMetadata)

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

  const normalizedViewerEmail = viewerEmail?.trim().toLowerCase()
  const normalizedSenderAddress = message.senderAddress?.trim().toLowerCase()
  const isViewerEmailSender = channel === 'email'
    && Boolean(normalizedViewerEmail)
    && Boolean(normalizedSenderAddress)
    && normalizedViewerEmail === normalizedSenderAddress
  const isViewerSender = !isAgent
    && !isPeer
    && (Boolean(message.clientId)
      || (message.senderUserId !== null && message.senderUserId !== undefined && message.senderUserId === viewerUserId)
      || isViewerEmailSender)

  let authorLabel = isAgent ? agentFirstName || 'Agent' : (isViewerSender ? 'You' : (message.senderName?.trim() || 'User'))
  if (isWebhook) {
    authorLabel = message.sourceLabel?.trim() || message.senderName?.trim() || 'Webhook'
  }
  if (isPeer) {
    authorLabel = peerDirectionLabel
  }

  let channelLabel = getChannelLabel(channel)
  let showChannelTag = channel !== 'web'
  if (isWebhook) {
    channelLabel = 'Webhook'
    showChannelTag = true
  }
  if (isPeer) {
    channelLabel = 'Peer DM'
    showChannelTag = true
  }

  const relativeLabel = message.relativeTimestamp || formatRelativeTimestamp(message.timestamp) || ''
  const status = message.status
  const statusLabel = status === 'sending' ? 'Sending...' : status === 'failed' ? 'Failed to send' : null
  const metaLabel = statusLabel || relativeLabel || message.timestamp || ''
  const metaTitle = message.error || message.timestamp || undefined
  const palette = !isPeer && !isAgent ? buildUserChatPalette(agentColorHex) : null
  const webhookMeta = isWebhook ? message.webhookMeta : null
  const webhookPayloadKind = (webhookMeta?.payloadKind || '').toLowerCase()
  const webhookPayloadObject = webhookMeta?.payload
  const shouldRenderWebhookJson = Boolean(
    isWebhook
    && (webhookPayloadKind === 'json' || webhookPayloadKind === 'form')
    && webhookPayloadObject !== undefined
    && webhookPayloadObject !== null,
  )
  const webhookJsonSrc = shouldRenderWebhookJson
    ? (typeof webhookPayloadObject === 'object' ? webhookPayloadObject : { value: webhookPayloadObject })
    : null
  const webhookMetaBits = [
    webhookMeta?.method?.trim()?.toUpperCase(),
    webhookMeta?.contentType?.trim() || null,
    webhookMeta?.queryParams && Object.keys(webhookMeta.queryParams).length > 0
      ? `${Object.keys(webhookMeta.queryParams).length} query param${Object.keys(webhookMeta.queryParams).length === 1 ? '' : 's'}`
      : null,
  ].filter(Boolean)

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
    <article
      className={`timeline-event chat-event ${alignmentClass} ${isPeer ? 'is-peer' : ''}`}
      data-cursor={eventCursor}
      data-message-id={message.id}
      data-status={status || undefined}
    >
      <div className={`chat-bubble ${bubbleTheme}`} style={bubbleStyle}>
        <div className={`chat-author ${authorTheme}`}>
          {isAgent && !isPeer ? (
            <AgentAvatarBadge
              name={agentFirstName || 'Agent'}
              avatarUrl={agentAvatarUrl}
              className="chat-author-avatar"
              imageClassName="chat-author-avatar-image"
              textClassName="chat-author-avatar-text"
              style={{ background: agentColorHex || '#6366f1' }}
            />
          ) : null}
          <span className="chat-author-name">{authorLabel}</span>
          {showChannelTag ? <span className={channelTagClass}>{channelLabel}</span> : null}
          <span className="chat-timestamp" title={metaTitle}>{metaLabel}</span>
        </div>
        {isWebhook && webhookMetaBits.length > 0 ? (
          <div className="mb-2 flex flex-wrap gap-2 text-[11px] font-medium text-slate-500">
            {webhookMetaBits.map((bit) => (
              <span key={bit} className="inline-flex items-center rounded-full border border-slate-200 bg-white/80 px-2 py-0.5">
                {bit}
              </span>
            ))}
          </div>
        ) : null}
        {shouldRenderWebhookJson && webhookJsonSrc ? (
          <div className="chat-content overflow-hidden rounded-xl border border-slate-200/80 bg-white/80 p-3">
            <ReactJsonView
              src={webhookJsonSrc}
              name={false}
              collapsed={1}
              displayDataTypes={false}
              displayObjectSize={false}
              enableClipboard={false}
              iconStyle="triangle"
              sortKeys
              style={{ backgroundColor: 'transparent', fontSize: '0.8125rem', lineHeight: 1.5 }}
            />
          </div>
        ) : (
          <div
            className={`chat-content prose prose-sm max-w-none leading-relaxed ${contentTone}`}
          >
            <MessageContent
              bodyHtml={message.bodyHtml}
              bodyText={message.bodyText}
              showEmptyState={!message.attachments || message.attachments.length === 0}
              animateIn={shouldAnimate}
              onLinkClick={onMessageLinkClick}
            />
          </div>
        )}
        {message.attachments && message.attachments.length > 0 ? (
          <div className="chat-attachments">
            {message.attachments.map((attachment) => {
              const href = attachment.downloadUrl || attachment.url
              const content = (
                <>
                  <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.415-6.414a4 4 0 10-5.657-5.657l-6.415 6.414" />
                  </svg>
                  <span className="truncate max-w-[160px]" title={attachment.filename}>
                    {attachment.filename}
                  </span>
                  {attachment.fileSizeLabel ? (
                    <span className={isAgent ? 'text-slate-500' : ''}>{attachment.fileSizeLabel}</span>
                  ) : null}
                </>
              )

              if (href) {
                return (
                  <a key={attachment.id} href={href} target="_blank" rel="noopener noreferrer">
                    {content}
                  </a>
                )
              }

              return (
                <span key={attachment.id} className="chat-attachment-pending" title={attachment.filename}>
                  {content}
                </span>
              )
            })}
          </div>
        ) : null}
      </div>
    </article>
  )
})
