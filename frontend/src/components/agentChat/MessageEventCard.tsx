import ReactJsonView from '@microlink/react-json-view'
import { memo, useCallback, useMemo, useState } from 'react'
import { Check, Copy, Flag, RotateCcw } from 'lucide-react'
import type { AgentMessage, AgentMessageFeedback } from './types'
import { MessageContent } from './MessageContent'
import { MessageFeedbackActions } from './MessageFeedbackActions'
import { AgentAvatarBadge } from '../common/AgentAvatarBadge'
import { useRelativeTimestamp } from '../../hooks/useRelativeTimestamp'
import { sanitizeHtml } from '../../util/sanitize'

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
  agentAvatarUrl?: string | null
  viewerUserId?: number | null
  viewerEmail?: string | null
  onMessageLinkClick?: (href: string) => boolean | void
  onMessageCopied?: (message: AgentMessage) => void | Promise<void>
  onMessageFeedback?: (message: AgentMessage, feedback: AgentMessageFeedback | null) => Promise<AgentMessageFeedback | null>
  onReportMessage?: (message: AgentMessage) => void
  onRetryMessage?: (message: AgentMessage) => void | Promise<void>
}

// Only animate messages that arrived recently (within last 3 seconds)
function isRecentMessage(timestamp?: string | null): boolean {
  if (!timestamp) return false
  const messageTime = Date.parse(timestamp)
  if (Number.isNaN(messageTime)) return false
  return Date.now() - messageTime < 3000
}

function plainTextFromHtml(html: string): string {
  if (typeof DOMParser === 'undefined') {
    return ''
  }
  const parser = new DOMParser()
  const doc = parser.parseFromString(html, 'text/html')
  return doc.body.textContent?.trim() || ''
}

async function writeMessageToClipboard(plainText: string, htmlText: string): Promise<void> {
  if (
    htmlText
    && typeof ClipboardItem !== 'undefined'
    && typeof navigator.clipboard.write === 'function'
  ) {
    await navigator.clipboard.write([
      new ClipboardItem({
        'text/plain': new Blob([plainText], { type: 'text/plain' }),
        'text/html': new Blob([htmlText], { type: 'text/html' }),
      }),
    ])
    return
  }

  await navigator.clipboard.writeText(plainText)
}

export const MessageEventCard = memo(function MessageEventCard({
  eventCursor,
  message,
  agentFirstName,
  agentAvatarUrl,
  viewerUserId,
  viewerEmail,
  onMessageLinkClick,
  onMessageCopied,
  onMessageFeedback,
  onReportMessage,
  onRetryMessage,
}: MessageEventCardProps) {
  const [copied, setCopied] = useState(false)
  const [retrying, setRetrying] = useState(false)
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
  const discordOutboundChannelLabel = channel === 'discord' && isAgent ? message.sourceLabel?.trim() : ''
  if (discordOutboundChannelLabel) {
    channelLabel = discordOutboundChannelLabel
  }
  let showChannelTag = channel !== 'web'
  if (isWebhook) {
    channelLabel = 'Webhook'
    showChannelTag = true
  }
  if (isPeer) {
    channelLabel = 'Peer DM'
    showChannelTag = true
  }

  const liveRelativeLabel = useRelativeTimestamp(message.timestamp)
  const relativeLabel = liveRelativeLabel || message.relativeTimestamp || ''
  const status = message.status
  const statusLabel = status === 'sending' ? 'Sending...' : status === 'failed' ? 'Failed to send' : null
  const metaLabel = statusLabel || relativeLabel || message.timestamp || ''
  const metaTitle = message.error || message.timestamp || undefined
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
  const emailSubject = channel === 'email' ? message.subject?.trim() : ''

  const contentTone = isPeer ? 'text-slate-800' : isAgent ? 'text-slate-800' : ''

  const alignmentClass = isPeer ? 'is-agent' : isAgent ? 'is-agent' : 'is-user'

  const channelTagBaseClass = 'ml-2 inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide'
  const channelTagClass = isPeer
    ? `${channelTagBaseClass} border border-indigo-200 bg-indigo-50 text-indigo-600`
    : isAgent
      ? `${channelTagBaseClass} border border-indigo-100 bg-indigo-50 text-indigo-600`
      : `${channelTagBaseClass} user-channel-badge`

  const showMessageActions = isAgent && !isPeer
  const showRetryAction = status === 'failed' && isViewerSender && Boolean(message.clientId) && Boolean(onRetryMessage)
  const clipboardHtml = useMemo(() => (
    message.bodyHtml?.trim() ? sanitizeHtml(message.bodyHtml) : ''
  ), [message.bodyHtml])
  const clipboardPlainText = useMemo(() => (
    message.bodyText?.trim() || (clipboardHtml ? plainTextFromHtml(clipboardHtml) : '')
  ), [clipboardHtml, message.bodyText])
  const copyDisabled = !clipboardPlainText && !clipboardHtml

  const handleCopyMessage = useCallback(async () => {
    if (copyDisabled) {
      return
    }
    try {
      await writeMessageToClipboard(clipboardPlainText, clipboardHtml)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1600)
      void onMessageCopied?.(message)
    } catch {
      setCopied(false)
    }
  }, [clipboardHtml, clipboardPlainText, copyDisabled, message, onMessageCopied])

  const handleReportMessage = useCallback(() => {
    onReportMessage?.(message)
  }, [message, onReportMessage])

  const handleRetryMessage = useCallback(async () => {
    if (!onRetryMessage || retrying) {
      return
    }
    setRetrying(true)
    try {
      await onRetryMessage(message)
    } finally {
      setRetrying(false)
    }
  }, [message, onRetryMessage, retrying])

  return (
    <article
      className={`timeline-event chat-event ${alignmentClass} ${isPeer ? 'is-peer' : ''}`}
      data-cursor={eventCursor}
      data-message-id={message.id}
      data-status={status || undefined}
    >
      <div className={`chat-bubble ${bubbleTheme}`}>
        <div className={`chat-author ${authorTheme}`}>
          {isAgent && !isPeer ? (
            <AgentAvatarBadge
              name={agentFirstName || 'Agent'}
              avatarUrl={agentAvatarUrl}
              className="chat-author-avatar"
              imageClassName="chat-author-avatar-image"
              textClassName="chat-author-avatar-text"
            />
          ) : null}
          <span className="chat-author-name">{authorLabel}</span>
          {showChannelTag ? <span className={channelTagClass}>{channelLabel}</span> : null}
          {emailSubject ? <span className="chat-email-subject-inline" title={emailSubject}>{emailSubject}</span> : null}
          <span className="chat-message-meta-slot">
            <span className="chat-timestamp" title={metaTitle}>{metaLabel}</span>
            {showMessageActions ? (
              <span className="chat-message-actions" aria-label="Message actions">
                <button
                  type="button"
                  className="chat-message-action-button"
                  onClick={handleCopyMessage}
                  disabled={copyDisabled}
                  title={copyDisabled ? 'No message text to copy' : copied ? 'Copied' : 'Copy message'}
                  aria-label={copyDisabled ? 'No message text to copy' : copied ? 'Copied message' : 'Copy message'}
                >
                  {copied ? <Check className="h-3.5 w-3.5" aria-hidden="true" /> : <Copy className="h-3.5 w-3.5" aria-hidden="true" />}
                </button>
                <MessageFeedbackActions message={message} onMessageFeedback={onMessageFeedback} />
                <button
                  type="button"
                  className="chat-message-action-button"
                  onClick={handleReportMessage}
                  title="Report issue"
                  aria-label="Report issue"
                >
                  <Flag className="h-3.5 w-3.5" aria-hidden="true" />
                </button>
              </span>
            ) : null}
          </span>
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
      {showRetryAction ? (
        <div className="chat-message-retry">
          <button
            type="button"
            className="chat-message-retry__button"
            onClick={handleRetryMessage}
            disabled={retrying}
          >
            <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
            <span>{retrying ? 'Retrying...' : 'Retry'}</span>
          </button>
        </div>
      ) : null}
    </article>
  )
})
