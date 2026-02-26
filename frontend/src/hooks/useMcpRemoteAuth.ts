import { useCallback, useEffect, useRef, useState } from 'react'

import { jsonRequest } from '../api/http'

type RemoteAuthStatus = 'idle' | 'starting' | 'pending' | 'authorized' | 'failed'

type UseMcpRemoteAuthOptions = {
  serverId?: string
  enabled: boolean
  startUrl: string
  statusUrlTemplate: string
}

type StartOptions = {
  source?: 'setup' | 'runtime'
}

type RemoteAuthState = {
  status: RemoteAuthStatus
  sessionId: string | null
  authorizationUrl: string | null
  error: string | null
}

const initialState: RemoteAuthState = {
  status: 'idle',
  sessionId: null,
  authorizationUrl: null,
  error: null,
}

const STATUS_PLACEHOLDER = 'remote-session-id'

function buildStatusUrl(template: string, sessionId: string): string {
  return template.replace(STATUS_PLACEHOLDER, encodeURIComponent(sessionId))
}

export function useMcpRemoteAuth(options: UseMcpRemoteAuthOptions) {
  const [state, setState] = useState<RemoteAuthState>(initialState)
  const pollTimerRef = useRef<number | null>(null)

  const stopPolling = useCallback(() => {
    if (pollTimerRef.current != null) {
      window.clearTimeout(pollTimerRef.current)
      pollTimerRef.current = null
    }
  }, [])

  const pollStatus = useCallback(
    async (sessionId: string) => {
      if (!options.enabled) {
        return
      }
      try {
        const payload = await jsonRequest<Record<string, unknown>>(buildStatusUrl(options.statusUrlTemplate, sessionId), {
          method: 'GET',
          includeCsrf: true,
        })
        const statusRaw = String(payload.status || '').toLowerCase()
        const authorizationUrl = String(payload.authorization_url || '')
        const error = String(payload.error || '')

        if (statusRaw === 'authorized') {
          setState((prev) => ({
            ...prev,
            status: 'authorized',
            authorizationUrl: authorizationUrl || prev.authorizationUrl,
            error: null,
          }))
          stopPolling()
          return
        }

        if (statusRaw === 'failed' || statusRaw === 'expired' || statusRaw === 'error') {
          setState((prev) => ({
            ...prev,
            status: 'failed',
            authorizationUrl: authorizationUrl || prev.authorizationUrl,
            error: error || 'Authorization failed.',
          }))
          stopPolling()
          return
        }

        setState((prev) => ({
          ...prev,
          status: 'pending',
          authorizationUrl: authorizationUrl || prev.authorizationUrl,
          error: null,
        }))
      } catch (error) {
        setState((prev) => ({
          ...prev,
          status: 'failed',
          error: error instanceof Error ? error.message : 'Unable to check authorization status.',
        }))
        stopPolling()
        return
      }

      pollTimerRef.current = window.setTimeout(() => {
        void pollStatus(sessionId)
      }, 1500)
    },
    [options.enabled, options.statusUrlTemplate, stopPolling],
  )

  const start = useCallback(
    async (params: StartOptions = {}) => {
      if (!options.enabled) {
        return { authorizationUrl: '' }
      }
      if (!options.serverId) {
        setState((prev) => ({ ...prev, status: 'failed', error: 'Save this server before connecting.' }))
        return { authorizationUrl: '' }
      }
      stopPolling()
      setState((prev) => ({ ...prev, status: 'starting', error: null }))
      try {
        const payload = await jsonRequest<Record<string, unknown>>(options.startUrl, {
          method: 'POST',
          includeCsrf: true,
          json: {
            server_config_id: options.serverId,
            source: params.source || 'setup',
          },
        })

        const sessionId = String(payload.session_id || '')
        if (!sessionId) {
          throw new Error('Remote auth session was not created.')
        }
        const authorizationUrl = String(payload.authorization_url || '')
        const statusRaw = String(payload.status || '').toLowerCase()
        const nextStatus: RemoteAuthStatus =
          statusRaw === 'authorized' ? 'authorized' : statusRaw === 'failed' || statusRaw === 'expired' ? 'failed' : 'pending'

        setState({
          status: nextStatus,
          sessionId,
          authorizationUrl: authorizationUrl || null,
          error: nextStatus === 'failed' ? String(payload.error || 'Authorization failed.') : null,
        })

        if (nextStatus === 'pending') {
          pollTimerRef.current = window.setTimeout(() => {
            void pollStatus(sessionId)
          }, 300)
        }
        return { authorizationUrl, sessionId }
      } catch (error) {
        const message = error instanceof Error ? error.message : 'Unable to start remote authorization.'
        setState((prev) => ({ ...prev, status: 'failed', error: message }))
        return { authorizationUrl: '' }
      }
    },
    [options.enabled, options.serverId, options.startUrl, pollStatus, stopPolling],
  )

  useEffect(() => {
    if (!options.enabled || !state.sessionId) {
      return
    }

    const key = `gobii:mcp_remote_auth_complete:${state.sessionId}`
    const listener = (event: StorageEvent) => {
      if (event.key !== key) {
        return
      }
      void pollStatus(state.sessionId!)
    }
    window.addEventListener('storage', listener)
    return () => {
      window.removeEventListener('storage', listener)
    }
  }, [options.enabled, pollStatus, state.sessionId])

  useEffect(
    () => () => {
      stopPolling()
    },
    [stopPolling],
  )

  useEffect(() => {
    if (!options.enabled) {
      setState(initialState)
      stopPolling()
    }
  }, [options.enabled, stopPolling])

  return {
    ...state,
    start,
    pollStatus,
  }
}
