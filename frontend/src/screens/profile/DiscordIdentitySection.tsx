import { useCallback, useEffect, useState } from 'react'
import { CheckCircle2, Link2, Trash2 } from 'lucide-react'

import { disconnectUserDiscordIdentity, fetchUserProfile } from '../../api/userProfile'
import type { DiscordIdentityState } from '../../api/userProfile'
import { safeErrorMessage } from '../../api/safeErrorMessage'

type DiscordIdentitySectionProps = {
  identity: DiscordIdentityState
  onChange: (identity: DiscordIdentityState | null) => void
}

type DiscordIdentityOAuthMessage = {
  type?: unknown
  status?: unknown
  message?: unknown
}

export function DiscordIdentitySection({ identity, onChange }: DiscordIdentitySectionProps) {
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const refreshIdentity = useCallback(async () => {
    const profile = await fetchUserProfile()
    onChange(profile.discordIdentity)
  }, [onChange])

  useEffect(() => {
    const handleOAuthComplete = (event: MessageEvent<DiscordIdentityOAuthMessage>) => {
      if (event.origin !== window.location.origin || event.data?.type !== 'gobii:discord_identity_oauth_complete') {
        return
      }
      if (event.data.status !== 'success') {
        setError(typeof event.data.message === 'string' ? event.data.message : 'Unable to link Discord account.')
        return
      }
      setBusy(true)
      setError(null)
      void refreshIdentity()
        .then(() => setMessage('Discord account verified.'))
        .catch((refreshError) => setError(safeErrorMessage(refreshError)))
        .finally(() => setBusy(false))
    }
    window.addEventListener('message', handleOAuthComplete)
    return () => window.removeEventListener('message', handleOAuthComplete)
  }, [refreshIdentity])

  const startLink = useCallback(() => {
    setMessage(null)
    setError(null)
    const popup = window.open(
      identity.connectUrl,
      'gobii-discord-identity',
      'popup=yes,width=520,height=720',
    )
    if (!popup) {
      setError('Allow popups to link your Discord account.')
    }
  }, [identity.connectUrl])

  const unlink = useCallback(async () => {
    if (!window.confirm('Unlink this Discord account? Discord messages from it will no longer be configuration-authorized.')) {
      return
    }
    setBusy(true)
    setMessage(null)
    setError(null)
    try {
      await disconnectUserDiscordIdentity(identity.disconnectUrl)
      await refreshIdentity()
      setMessage('Discord account unlinked.')
    } catch (unlinkError) {
      setError(safeErrorMessage(unlinkError))
    } finally {
      setBusy(false)
    }
  }, [identity.disconnectUrl, refreshIdentity])

  const accountLabel = identity.displayName || (identity.username ? `@${identity.username}` : 'Discord account')

  return (
    <section className="profile-screen__section">
      <div className="profile-screen__section-header">
        <div className="profile-screen__section-icon" aria-hidden="true">
          <img src="/static/images/integrations/native/discord.svg" alt="" className="h-4 w-4 object-contain" />
        </div>
        <div>
          <h2>Discord Identity</h2>
          <p>Verify who you are when messaging agents from Discord.</p>
        </div>
      </div>

      {identity.linked ? (
        <>
          <div className="profile-screen__status-row">
            <div>
              <p className="profile-screen__phone-number">{accountLabel}</p>
              {identity.displayName && identity.username ? (
                <p className="profile-screen__muted">@{identity.username}</p>
              ) : null}
            </div>
            <span className="profile-screen__status profile-screen__status--success">
              <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
              Verified
            </span>
          </div>
          <p className="profile-screen__muted">
            {identity.canConfigureInCurrentContext
              ? 'Messages from this Discord account can manage durable agent configuration in the current workspace.'
              : identity.contextConnected
                ? 'Your Discord identity is verified, but your current team role cannot manage durable agent configuration.'
                : 'Your identity will apply in workspaces where Discord is connected and your role can manage agents.'}
          </p>
          <div className="profile-screen__button-row">
            <button type="button" className="profile-screen__button profile-screen__button--secondary" onClick={startLink} disabled={busy}>
              <Link2 className="h-4 w-4" aria-hidden="true" />
              Re-link
            </button>
            <button type="button" className="profile-screen__button profile-screen__button--danger" onClick={() => void unlink()} disabled={busy}>
              <Trash2 className="h-4 w-4" aria-hidden="true" />
              Unlink
            </button>
          </div>
        </>
      ) : (
        <>
          <p className="profile-screen__muted">
            Link your Discord account so agents can match its verified Discord ID to your Gobii account. Usernames alone are never trusted.
          </p>
          <div className="profile-screen__button-row">
            <button type="button" className="profile-screen__button profile-screen__button--primary" onClick={startLink} disabled={busy}>
              <Link2 className="h-4 w-4" aria-hidden="true" />
              Link Discord Account
            </button>
          </div>
        </>
      )}

      {message ? <p className="profile-screen__feedback profile-screen__feedback--success">{message}</p> : null}
      {error ? <p className="profile-screen__feedback profile-screen__feedback--error">{error}</p> : null}
    </section>
  )
}
