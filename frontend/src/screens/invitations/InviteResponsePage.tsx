import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, ArrowRight, CheckCircle2, LoaderCircle, XCircle } from 'lucide-react'

type InviteStatus = 'loading' | 'accepted' | 'declined' | 'issue' | 'error'

export type InviteResponsePayload = {
  ok: boolean
  issue?: string
  redirectUrl?: string
}

type InviteResponseContext<TPayload> = {
  status: InviteStatus
  payload: TPayload | null
  errorMessage: string | null
}

export type InviteResponseConfig<TPayload extends InviteResponsePayload> = {
  eyebrow: string
  successStatus: 'accepted' | 'declined'
  request: (token: string) => Promise<TPayload>
  title: (context: InviteResponseContext<TPayload>) => string
  message: (context: InviteResponseContext<TPayload>) => string
  actionPath: (context: InviteResponseContext<TPayload>) => string
  actionLabel: (context: InviteResponseContext<TPayload>) => string
  errorMessage: string
  onSuccess?: (payload: TPayload) => void
}

export function InviteResponsePage<TPayload extends InviteResponsePayload>({
  token,
  onNavigate,
  config,
}: {
  token: string
  onNavigate: (path: string) => void
  config: InviteResponseConfig<TPayload>
}) {
  const [status, setStatus] = useState<InviteStatus>('loading')
  const [payload, setPayload] = useState<TPayload | null>(null)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    let redirectTimer: number | undefined

    const respondToInvite = async () => {
      setStatus('loading')
      setPayload(null)
      setErrorMessage(null)
      try {
        const result = await config.request(token)
        if (cancelled) return
        setPayload(result)
        if (!result.ok) {
          setStatus('issue')
          return
        }
        setStatus(config.successStatus)
        config.onSuccess?.(result)
        if (result.redirectUrl) {
          redirectTimer = window.setTimeout(() => onNavigate(result.redirectUrl as string), 650)
        }
      } catch (error) {
        if (cancelled) return
        setStatus('error')
        setErrorMessage(error instanceof Error ? error.message : config.errorMessage)
      }
    }

    void respondToInvite()
    return () => {
      cancelled = true
      if (redirectTimer) window.clearTimeout(redirectTimer)
    }
  }, [config, onNavigate, token])

  const icon = useMemo(() => {
    if (status === 'loading') {
      return <LoaderCircle className="immersive-invite-card__icon-svg immersive-invite-card__icon-svg--spin" />
    }
    if (status === 'accepted') return <CheckCircle2 className="immersive-invite-card__icon-svg" />
    if (status === 'declined') return <XCircle className="immersive-invite-card__icon-svg" />
    return <AlertTriangle className="immersive-invite-card__icon-svg" />
  }, [status])
  const context = { status, payload, errorMessage }

  return (
    <section className="immersive-invite-page">
      <div className={`immersive-invite-card immersive-invite-card--${status}`}>
        <div className="immersive-invite-card__icon">{icon}</div>
        <p className="immersive-invite-card__eyebrow">{config.eyebrow}</p>
        <h1 className="immersive-invite-card__title">{config.title(context)}</h1>
        <p className="immersive-invite-card__message">{config.message(context)}</p>
        {status !== 'loading' ? (
          <button
            type="button"
            className="immersive-invite-card__button"
            onClick={() => onNavigate(config.actionPath(context))}
          >
            <span>{config.actionLabel(context)}</span>
            <ArrowRight className="h-4 w-4" />
          </button>
        ) : null}
      </div>
    </section>
  )
}
