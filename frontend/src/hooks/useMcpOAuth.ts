import { useCallback, useEffect, useMemo, useState } from 'react'

import { jsonRequest } from '../api/http'

const STATE_KEY_PREFIX = 'gobii:mcp_oauth_state:'
const SERVER_KEY_PREFIX = 'gobii:mcp_oauth_server:'

type OAuthStatus = 'idle' | 'loading' | 'pending' | 'connected' | 'disconnected'

type UseMcpOAuthOptions = {
  serverId?: string
  authMethod: string
  startUrl: string
  metadataUrl: string
  callbackPath: string
  statusUrl?: string
  revokeUrl?: string
  getServerUrl: () => string
}

type StartOAuthParams = {
  clientId?: string
  clientSecret?: string
  scope?: string
}

type OAuthState = {
  status: OAuthStatus
  scope: string | null
  expiresAt: string | null
  connecting: boolean
  revoking: boolean
  error: string | null
  requiresManualClient: boolean
}

const initialState: OAuthState = {
  status: 'idle',
  scope: null,
  expiresAt: null,
  connecting: false,
  revoking: false,
  error: null,
  requiresManualClient: true,
}

export function useMcpOAuth(options: UseMcpOAuthOptions) {
  const [state, setState] = useState<OAuthState>(initialState)

  const shouldEnable = options.authMethod === 'oauth2'

  const pendingInfo = useMemo(() => {
    if (!options.serverId) {
      return null
    }
    const raw = localStorage.getItem(`${SERVER_KEY_PREFIX}${options.serverId}`)
    if (!raw) {
      return null
    }
    try {
      return JSON.parse(raw) as { state?: string }
    } catch (error) {
      console.warn('Invalid pending OAuth payload', error)
      return null
    }
  }, [options.serverId])

  const refreshStatus = useCallback(async () => {
    if (!shouldEnable || !options.serverId || !options.statusUrl) {
      setState((prev) => ({ ...prev, status: options.serverId ? 'idle' : 'idle', scope: null, expiresAt: null }))
      return
    }
    setState((prev) => ({ ...prev, status: 'loading', error: null }))
    try {
      const payload = await jsonRequest<{ connected: boolean; scope?: string; expires_at?: string }>(options.statusUrl)
      if (payload.connected) {
        setState((prev) => ({
          ...prev,
          status: 'connected',
          scope: payload.scope || null,
          expiresAt: payload.expires_at || null,
        }))
      } else {
        setState((prev) => ({ ...prev, status: pendingInfo ? 'pending' : 'disconnected', scope: null, expiresAt: null }))
      }
    } catch (error) {
      console.warn('Failed to load OAuth status', error)
      setState((prev) => ({ ...prev, status: pendingInfo ? 'pending' : 'disconnected' }))
    }
  }, [options.serverId, options.statusUrl, pendingInfo, shouldEnable])

  useEffect(() => {
    if (!options.serverId || !shouldEnable) {
      setState(initialState)
      return
    }
    refreshStatus()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [options.serverId, shouldEnable, refreshStatus])

  const startOAuth = useCallback(
    async ({ clientId, clientSecret, scope }: StartOAuthParams) => {
      if (!shouldEnable) {
        setState((prev) => ({ ...prev, error: 'Select OAuth 2.0 to enable this flow.' }))
        return
      }
      if (!options.serverId) {
        setState((prev) => ({ ...prev, error: 'Save this MCP server first, then return to connect.' }))
        return
      }
      const serverUrl = options.getServerUrl().trim()
      if (!serverUrl) {
        setState((prev) => ({ ...prev, error: 'Enter the MCP server URL before connecting.' }))
        return
      }

      try {
        setState((prev) => ({ ...prev, connecting: true, error: null }))
        const metadata = await jsonRequest<Record<string, unknown>>(options.metadataUrl, {
          method: 'POST',
          includeCsrf: true,
          json: {
            server_config_id: options.serverId,
            resource: '/.well-known/oauth-authorization-server',
          },
        })

        const authorizationEndpoint = String(metadata.authorization_endpoint || '')
        const tokenEndpoint = String(metadata.token_endpoint || '')
        if (!authorizationEndpoint || !tokenEndpoint) {
          throw new Error('OAuth metadata is missing authorization or token endpoints.')
        }

        const requiresManualClient = !metadata.registration_endpoint
        setState((prev) => ({ ...prev, requiresManualClient }))
        if (requiresManualClient && !clientId) {
          setState((prev) => ({ ...prev, error: 'Provide an OAuth client ID.' }))
          return
        }

        const pkce = await generatePkcePair()
        const stateToken = randomState()
        const callbackUrl = new URL(options.callbackPath, window.location.origin).toString()

        const startPayload: Record<string, unknown> = {
          server_config_id: options.serverId,
          scope,
          token_endpoint: tokenEndpoint,
          code_challenge: pkce.challenge,
          code_challenge_method: 'S256',
          code_verifier: pkce.verifier,
          redirect_uri: callbackUrl,
          state: stateToken,
          metadata,
        }

        if (requiresManualClient) {
          startPayload.client_id = clientId
          startPayload.client_secret = clientSecret
        }

        const session = await jsonRequest<{ session_id: string; state: string; client_id?: string }>(options.startUrl, {
          method: 'POST',
          includeCsrf: true,
          json: startPayload,
        })

        const authorizationClientId = requiresManualClient ? clientId : session.client_id
        if (!authorizationClientId) {
          throw new Error('OAuth server did not provide a client ID.')
        }

        const redirectUrl = new URL(authorizationEndpoint)
        redirectUrl.searchParams.set('response_type', 'code')
        redirectUrl.searchParams.set('client_id', authorizationClientId)
        redirectUrl.searchParams.set('redirect_uri', callbackUrl)
        redirectUrl.searchParams.set('state', session.state)
        if (scope) {
          redirectUrl.searchParams.set('scope', scope)
        }
        redirectUrl.searchParams.set('code_challenge', pkce.challenge)
        redirectUrl.searchParams.set('code_challenge_method', 'S256')

        storePendingState(session.state, {
          sessionId: session.session_id,
          serverId: options.serverId,
          returnUrl: window.location.href,
        })
        storeServerPending(options.serverId, {
          state: session.state,
          sessionId: session.session_id,
          created_at: Date.now(),
        })

        setState((prev) => ({ ...prev, status: 'pending' }))
        window.location.href = redirectUrl.toString()
      } catch (error) {
        setState((prev) => ({
          ...prev,
          connecting: false,
          error: error instanceof Error ? error.message : 'Failed to start OAuth flow.',
        }))
      }
    },
    [options, shouldEnable],
  )

  const revokeOAuth = useCallback(async () => {
    if (!options.revokeUrl) {
      return
    }
    setState((prev) => ({ ...prev, revoking: true, error: null }))
    try {
      await jsonRequest(options.revokeUrl, {
        method: 'POST',
        includeCsrf: true,
        json: {},
      })
      if (options.serverId) {
        clearServerPending(options.serverId)
      }
      setState((prev) => ({ ...prev, status: 'disconnected', scope: null, expiresAt: null }))
    } catch (error) {
      setState((prev) => ({
        ...prev,
        error: error instanceof Error ? error.message : 'Failed to revoke credentials.',
      }))
    } finally {
      setState((prev) => ({ ...prev, revoking: false }))
    }
  }, [options.revokeUrl, options.serverId])

  return {
    ...state,
    startOAuth,
    revokeOAuth,
    refreshStatus,
  }
}

function base64UrlEncode(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer)
  let binary = ''
  bytes.forEach((byte) => {
    binary += String.fromCharCode(byte)
  })
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

async function generatePkcePair() {
  const verifierBytes = new Uint8Array(64)
  crypto.getRandomValues(verifierBytes)
  const verifier = base64UrlEncode(verifierBytes.buffer)
  const data = new TextEncoder().encode(verifier)
  const digest = await crypto.subtle.digest('SHA-256', data)
  const challenge = base64UrlEncode(digest)
  return { verifier, challenge }
}

function randomState(): string {
  const bytes = new Uint8Array(16)
  crypto.getRandomValues(bytes)
  return base64UrlEncode(bytes.buffer)
}

function storePendingState(state: string, payload: Record<string, unknown>) {
  try {
    localStorage.setItem(`${STATE_KEY_PREFIX}${state}`, JSON.stringify(payload))
  } catch (error) {
    console.warn('Failed to persist OAuth state', error)
  }
}

function storeServerPending(serverId: string, payload: Record<string, unknown>) {
  try {
    localStorage.setItem(`${SERVER_KEY_PREFIX}${serverId}`, JSON.stringify(payload))
  } catch (error) {
    console.warn('Failed to persist server OAuth payload', error)
  }
}

function clearServerPending(serverId: string) {
  localStorage.removeItem(`${SERVER_KEY_PREFIX}${serverId}`)
}
