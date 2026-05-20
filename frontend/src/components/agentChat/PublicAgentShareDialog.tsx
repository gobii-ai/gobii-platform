import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react'
import { Check, Copy, Globe, Loader2, Share2 } from 'lucide-react'

import {
  cloneAgentTemplate,
  fetchAgentTemplateShareInfo,
  type TemplateShareInfoResponse,
} from '../../api/agentTemplates'
import { Modal } from '../common/Modal'
import { AgentChatMobileSheet } from './AgentChatMobileSheet'

type PublicAgentShareDialogProps = {
  open: boolean
  agentId?: string | null
  agentName?: string | null
  onClose: () => void
}

function describeShareError(error: unknown): string {
  if (error instanceof Error && error.message) {
    return error.message
  }
  return 'Unable to update sharing right now.'
}

export function PublicAgentShareDialog({
  open,
  agentId,
  agentName,
  onClose,
}: PublicAgentShareDialogProps) {
  const [shareInfo, setShareInfo] = useState<TemplateShareInfoResponse | null>(null)
  const [handleInput, setHandleInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [copied, setCopied] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [isMobile, setIsMobile] = useState(false)

  const displayName = useMemo(() => {
    return (shareInfo?.agentName || agentName || '').trim() || 'this agent'
  }, [agentName, shareInfo?.agentName])
  const title = `Share ${displayName}`
  const hasTemplate = Boolean(shareInfo?.templateUrl)
  const hasProfile = Boolean(shareInfo?.publicProfileHandle)
  const shortUrl = shareInfo?.templateUrl?.replace(/^https?:\/\//, '') ?? ''

  useEffect(() => {
    const checkMobile = () => {
      setIsMobile(window.innerWidth < 768)
    }
    checkMobile()
    window.addEventListener('resize', checkMobile)
    return () => window.removeEventListener('resize', checkMobile)
  }, [])

  useEffect(() => {
    if (!open) {
      setShareInfo(null)
      setHandleInput('')
      setError(null)
      setCopied(false)
      return
    }
    if (!agentId) {
      setError('Choose an agent before sharing.')
      return
    }

    let cancelled = false
    setLoading(true)
    setError(null)
    void fetchAgentTemplateShareInfo(agentId)
      .then((payload) => {
        if (cancelled) return
        setShareInfo(payload)
        setHandleInput(payload.publicProfileHandle ?? payload.suggestedHandle ?? '')
      })
      .catch((err) => {
        if (cancelled) return
        setError(describeShareError(err))
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [agentId, open])

  const handleCreateShare = useCallback(async (event?: FormEvent<HTMLFormElement>) => {
    event?.preventDefault()
    if (!agentId || !shareInfo?.canShare) {
      return
    }
    const handle = hasProfile ? null : handleInput.trim()
    if (!hasProfile && !handle) {
      setError('Choose a public handle to continue.')
      return
    }

    setBusy(true)
    setError(null)
    try {
      const result = await cloneAgentTemplate(agentId, handle)
      setShareInfo((current) => ({
        agentId,
        agentName: current?.agentName ?? displayName,
        canShare: true,
        disabledReason: null,
        publicProfileHandle: result.publicProfileHandle,
        suggestedHandle: null,
        templateUrl: result.templateUrl,
        templateSlug: result.templateSlug,
        displayName: result.displayName ?? current?.displayName ?? null,
      }))
      setHandleInput(result.publicProfileHandle)
    } catch (err) {
      setError(describeShareError(err))
    } finally {
      setBusy(false)
    }
  }, [agentId, displayName, handleInput, hasProfile, shareInfo?.canShare])

  const handleCopy = useCallback(async () => {
    const url = shareInfo?.templateUrl
    if (!url) return
    try {
      await navigator.clipboard.writeText(url)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1600)
    } catch {
      setError('Unable to copy the link.')
    }
  }, [shareInfo?.templateUrl])

  if (!open) {
    return null
  }

  const body = (
    <div className="space-y-4">
      {loading ? (
        <div className="flex items-center gap-2 rounded-xl bg-white/70 px-3 py-3 text-sm text-[#5f3b78]">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          <span>Loading share settings...</span>
        </div>
      ) : shareInfo?.canShare === false ? (
        <div className="rounded-xl bg-white/75 px-3 py-3 text-sm text-[#5f3b78]">
          {shareInfo.disabledReason || 'Sharing is unavailable for this agent.'}
        </div>
      ) : hasTemplate ? (
        <div className="space-y-3">
          <div className="rounded-xl bg-white/75 px-3 py-3">
            <div className="flex items-center gap-2 text-sm font-semibold text-[#3d2551]">
              <Check className="h-4 w-4 text-[#7c4ca0]" aria-hidden="true" />
              <span>Public link active</span>
            </div>
            <a
              href={shareInfo?.templateUrl ?? '#'}
              target="_blank"
              rel="noreferrer"
              className="mt-2 block truncate text-sm font-medium text-[#6f4690] hover:text-[#4b2d64]"
            >
              {shortUrl}
            </a>
          </div>
          <button
            type="button"
            className="inline-flex w-full items-center justify-center gap-2 rounded-xl bg-[#AA74CE] px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-[#965dbb]"
            onClick={handleCopy}
          >
            {copied ? <Check className="h-4 w-4" aria-hidden="true" /> : <Copy className="h-4 w-4" aria-hidden="true" />}
            <span>{copied ? 'Copied' : 'Copy link'}</span>
          </button>
        </div>
      ) : (
        <form className="space-y-3" onSubmit={handleCreateShare}>
          {!hasProfile ? (
            <label className="block text-sm font-medium text-[#3d2551]" htmlFor="public-share-handle">
              Public handle
              <span className="mt-1 flex items-center rounded-xl bg-white/80 px-3 py-2">
                <span className="text-sm text-[#7a5a91]">gobii.ai/</span>
                <input
                  id="public-share-handle"
                  type="text"
                  value={handleInput}
                  onChange={(event) => setHandleInput(event.currentTarget.value.toLowerCase().replace(/[^a-z0-9-]/g, ''))}
                  className="min-w-0 flex-1 border-0 bg-transparent p-0 text-sm text-[#3d2551] outline-none focus:ring-0"
                  placeholder="your-handle"
                  disabled={busy}
                />
              </span>
            </label>
          ) : null}
          <button
            type="submit"
            className="inline-flex w-full items-center justify-center gap-2 rounded-xl bg-[#AA74CE] px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-[#965dbb] disabled:cursor-not-allowed disabled:opacity-60"
            disabled={busy || (!hasProfile && !handleInput.trim())}
          >
            {busy ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <Share2 className="h-4 w-4" aria-hidden="true" />}
            <span>{busy ? 'Creating...' : 'Create public link'}</span>
          </button>
        </form>
      )}
      {error ? <p className="text-sm text-rose-600">{error}</p> : null}
    </div>
  )

  if (isMobile) {
    return (
      <AgentChatMobileSheet
        open={open}
        onClose={onClose}
        title={title}
        subtitle="Create a public template link that others can copy."
        icon={Globe}
        ariaLabel={title}
      >
        {body}
      </AgentChatMobileSheet>
    )
  }

  return (
    <Modal
      title={title}
      subtitle="Create a public template link that others can copy."
      onClose={onClose}
      icon={Globe}
      iconBgClass="bg-[#f4eafb]"
      iconColorClass="text-[#7c4ca0]"
      widthClass="sm:max-w-lg"
      bodyClassName="space-y-4"
    >
      {body}
    </Modal>
  )
}
