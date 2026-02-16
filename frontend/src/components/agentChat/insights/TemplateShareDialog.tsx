import { useEffect, useMemo, useState } from 'react'
import { Check, Copy, Share } from 'lucide-react'

import { Modal } from '../../common/Modal'
import { AgentChatMobileSheet } from '../AgentChatMobileSheet'

type TemplateShareDialogProps = {
  open: boolean
  templateUrl: string
  agentName?: string | null
  copied: boolean
  onCopy: (value: string) => void | Promise<void>
  onClose: () => void
}

type ShareTarget = {
  key: string
  label: string
  href: string
}

function buildShareTargets(templateUrl: string, shareText: string): ShareTarget[] {
  const encodedUrl = encodeURIComponent(templateUrl)
  const encodedText = encodeURIComponent(shareText)

  return [
    {
      key: 'reddit',
      label: 'Reddit',
      href: `https://www.reddit.com/submit?url=${encodedUrl}&title=${encodedText}`,
    },
    {
      key: 'facebook',
      label: 'Facebook',
      href: `https://www.facebook.com/sharer/sharer.php?u=${encodedUrl}`,
    },
    {
      key: 'linkedin',
      label: 'LinkedIn',
      href: `https://www.linkedin.com/sharing/share-offsite/?url=${encodedUrl}`,
    },
    {
      key: 'x',
      label: 'X',
      href: `https://twitter.com/intent/tweet?url=${encodedUrl}&text=${encodedText}`,
    },
  ]
}

export function TemplateShareDialog({
  open,
  templateUrl,
  agentName,
  copied,
  onCopy,
  onClose,
}: TemplateShareDialogProps) {
  const [isMobile, setIsMobile] = useState(false)

  useEffect(() => {
    const checkMobile = () => setIsMobile(window.innerWidth < 768)
    checkMobile()
    window.addEventListener('resize', checkMobile)
    return () => window.removeEventListener('resize', checkMobile)
  }, [])

  const shareText = useMemo(() => {
    const displayName = (agentName || '').trim() || 'my agent'
    return `Check out ${displayName} on Gobii`
  }, [agentName])

  const shareTargets = useMemo(() => buildShareTargets(templateUrl, shareText), [templateUrl, shareText])
  if (!open || !templateUrl) {
    return null
  }

  const body = (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-2">
        {shareTargets.map((target) => (
          <a
            key={target.key}
            href={target.href}
            target="_blank"
            rel="noreferrer"
            onClick={onClose}
            className="inline-flex items-center justify-center rounded-lg border border-indigo-300 bg-indigo-50 px-3 py-2 text-sm font-semibold text-indigo-700 transition hover:bg-indigo-100"
          >
            {target.label}
          </a>
        ))}
      </div>

      <button
        type="button"
        onClick={() => {
          void onCopy(templateUrl)
        }}
        className={`inline-flex w-full items-center justify-center gap-2 rounded-lg px-3 py-2 text-sm font-semibold transition ${
          copied
            ? 'border border-emerald-300 bg-emerald-50 text-emerald-700'
            : 'border border-indigo-300 bg-white text-indigo-700 hover:bg-indigo-50'
        }`}
      >
        {copied ? <Check size={16} /> : <Copy size={16} />}
        <span>{copied ? 'Copied!' : 'Copy link'}</span>
      </button>
    </div>
  )

  const title = 'Share...'
  const subtitle = 'Share this public agent link anywhere.'

  if (isMobile) {
    return (
      <AgentChatMobileSheet
        open={open}
        onClose={onClose}
        title={title}
        subtitle={subtitle}
        icon={Share}
        ariaLabel={title}
      >
        {body}
      </AgentChatMobileSheet>
    )
  }

  return (
    <Modal
      title={title}
      subtitle={subtitle}
      onClose={onClose}
      icon={Share}
      iconBgClass="bg-indigo-100"
      iconColorClass="text-indigo-700"
      widthClass="sm:max-w-lg"
      bodyClassName="space-y-4"
    >
      {body}
    </Modal>
  )
}
