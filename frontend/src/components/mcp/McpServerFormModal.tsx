import { Fragment, useEffect, useState } from 'react'
import { Loader2, ServerCog } from 'lucide-react'

import type { McpServerDetail, McpServerPayload } from '../../api/mcp'
import { Modal } from '../common/Modal'
import { useMcpOAuth } from '../../hooks/useMcpOAuth'

type HeaderEntry = { key: string; value: string }

type FormErrors = Record<string, string[]>

type McpServerFormModalProps = {
  mode: 'create' | 'edit'
  server?: McpServerDetail
  loading?: boolean
  isSubmitting: boolean
  ownerScope?: string
  onClose: () => void
  onSubmit: (payload: McpServerPayload) => Promise<void>
  errorResponse?: FormErrors | null
  oauth: {
    startUrl: string
    metadataUrl: string
    callbackPath: string
  }
}

type FormState = {
  displayName: string
  slug: string
  url: string
  isActive: boolean
  authMethod: string
  headers: HeaderEntry[]
}

const BLANK_HEADER: HeaderEntry = { key: '', value: '' }

const createBlankHeaders = (): HeaderEntry[] => [{ ...BLANK_HEADER }]

export function McpServerFormModal({
  mode,
  server,
  loading,
  isSubmitting,
  ownerScope,
  onClose,
  onSubmit,
  errorResponse,
  oauth,
}: McpServerFormModalProps) {
  const [state, setState] = useState<FormState>(() => getInitialState(server))
  const [clientId, setClientId] = useState('')
  const [clientSecret, setClientSecret] = useState('')
  const [oauthScope, setOauthScope] = useState('')
  const [useCustomClient, setUseCustomClient] = useState(false)

  const oauthStore = useMcpOAuth({
    serverId: mode === 'edit' ? server?.id : undefined,
    authMethod: state.authMethod,
    startUrl: oauth.startUrl,
    metadataUrl: oauth.metadataUrl,
    callbackPath: oauth.callbackPath,
    statusUrl: server?.oauthStatusUrl,
    revokeUrl: server?.oauthRevokeUrl,
    getServerUrl: () => state.url,
  })

  useEffect(() => {
    if (server) {
      setState(getInitialState(server))
    }
  }, [server])

  useEffect(() => {
    if (oauthStore.requiresManualClient) {
      setUseCustomClient(true)
    }
  }, [oauthStore.requiresManualClient])

  const nonFieldErrors = errorResponse?.non_field_errors || []

  const handleDisplayNameChange = (value: string) => {
    setState((prev) => ({
      ...prev,
      displayName: value,
      slug: mode === 'create' ? slugify(value) : prev.slug,
    }))
  }

  const handleHeaderChange = (index: number, key: 'key' | 'value', value: string) => {
    setState((prev) => {
      const headers = [...prev.headers]
      headers[index] = { ...headers[index], [key]: value }
      return { ...prev, headers }
    })
  }

  const addHeaderRow = () => {
    setState((prev) => ({ ...prev, headers: [...prev.headers, { key: '', value: '' }] }))
  }

  const removeHeaderRow = (index: number) => {
    setState((prev) => {
      const headers = prev.headers.filter((_, idx) => idx !== index)
      return { ...prev, headers: headers.length ? headers : createBlankHeaders() }
    })
  }

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault()
    const payload: McpServerPayload = {
      display_name: state.displayName.trim(),
      name: state.slug.trim(),
      url: state.url.trim(),
      auth_method: state.authMethod,
      is_active: state.isActive,
      headers: headersToObject(state.headers),
      metadata: {},
      environment: {},
      command: '',
      command_args: [],
    }
    await onSubmit(payload)
  }

  const formTitle = mode === 'create' ? 'Add MCP Server' : `Edit ${server?.displayName ?? 'MCP Server'}`

  const ownerLabelText = ownerScope === 'organization' ? 'your organization' : 'your workspace'
  const modalSubtitle =
    mode === 'create'
      ? `Connect a new MCP integration for ${ownerLabelText}.`
      : `Update connection and OAuth settings for ${server?.displayName ?? 'this MCP server'}.`

  if (loading) {
    return (
      <Modal
        title={formTitle}
        subtitle={modalSubtitle}
        onClose={onClose}
        icon={Loader2}
        iconBgClass="bg-indigo-100"
        iconColorClass="text-indigo-700"
      >
        <div className="flex items-center gap-2 py-6 text-sm text-slate-500">
          <Loader2 className="h-4 w-4 animate-spin" />
          Loading configuration…
        </div>
      </Modal>
    )
  }

  const footer = (
    <Fragment>
      <button
        type="submit"
        form="mcp-server-form"
        className="inline-flex w-full justify-center rounded-md border border-transparent bg-blue-600 px-4 py-2 text-base font-medium text-white shadow-sm transition hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 sm:ml-3 sm:w-auto sm:text-sm disabled:opacity-60"
        disabled={isSubmitting}
      >
        {isSubmitting ? 'Saving…' : 'Save Server'}
      </button>
      <button
        type="button"
        className="inline-flex w-full justify-center rounded-md border border-slate-300 bg-white px-4 py-2 text-base font-medium text-slate-700 shadow-sm transition hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 sm:w-auto sm:text-sm"
        onClick={onClose}
      >
        Cancel
      </button>
    </Fragment>
  )

  return (
    <Modal
      title={formTitle}
      subtitle={modalSubtitle}
      onClose={onClose}
      footer={footer}
      widthClass="sm:max-w-3xl"
      icon={ServerCog}
    >
      <form id="mcp-server-form" className="space-y-6" onSubmit={handleSubmit}>
        {nonFieldErrors.length > 0 && (
          <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {nonFieldErrors.map((error) => (
              <p key={error}>{error}</p>
            ))}
          </div>
        )}

        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-slate-700">Display Name</label>
            <input
              type="text"
              className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:ring-blue-500"
              value={state.displayName}
              onChange={(event) => handleDisplayNameChange(event.target.value)}
              required
            />
            <p className="text-xs text-slate-500">Identifier: <span className="font-mono text-slate-700">{state.slug || 'auto-generated'}</span></p>
            {getFieldErrors('display_name', errorResponse).map((error) => (
              <p key={error} className="text-xs text-red-600">
                {error}
              </p>
            ))}
            {getFieldErrors('name', errorResponse).map((error) => (
              <p key={error} className="text-xs text-red-600">
                {error}
              </p>
            ))}
          </div>

          <div className="flex items-center gap-3">
            <label className="flex items-center gap-2 text-sm font-medium text-slate-700">
              <input
                type="checkbox"
                className="h-4 w-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500"
                checked={state.isActive}
                onChange={(event) => setState((prev) => ({ ...prev, isActive: event.target.checked }))}
              />
              Active
            </label>
            <span className="text-xs text-slate-500">Inactive servers remain hidden from agents.</span>
          </div>

          <div>
            <label className="block text-sm font-medium text-slate-700">Authentication</label>
            <select
              className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:ring-blue-500"
              value={state.authMethod}
              onChange={(event) => setState((prev) => ({ ...prev, authMethod: event.target.value }))}
            >
              <option value="none">None</option>
              <option value="bearer_token">Bearer Token</option>
              <option value="oauth2">OAuth 2.0</option>
            </select>
            {getFieldErrors('auth_method', errorResponse).map((error) => (
              <p key={error} className="text-xs text-red-600">
                {error}
              </p>
            ))}
          </div>

          {ownerScope === 'organization' && (
            <div className="rounded-lg border border-amber-100 bg-amber-50 px-4 py-3 text-xs text-amber-800">
              Only members with manage permissions can update organization servers.
            </div>
          )}
        </div>

        <div className="rounded-lg border border-slate-200 bg-white px-4 py-4">
          <label className="block text-sm font-semibold text-slate-700">URL</label>
          <input
            type="url"
            className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:ring-blue-500"
            value={state.url}
            onChange={(event) => setState((prev) => ({ ...prev, url: event.target.value }))}
            required
          />
          <p className="text-xs text-slate-500">HTTPS URL for the remote MCP server.</p>
          {getFieldErrors('url', errorResponse).map((error) => (
            <p key={error} className="text-xs text-red-600">
              {error}
            </p>
          ))}
        </div>

        <div className="rounded-lg border border-slate-200 bg-white px-4 py-4">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm font-semibold text-slate-700">Headers</p>
              <p className="text-xs text-slate-500">Encrypted and stored securely.</p>
            </div>
            <button
              type="button"
              className="text-sm font-medium text-blue-600 hover:text-blue-700"
              onClick={addHeaderRow}
            >
              Add Header
            </button>
          </div>
          <div className="mt-4 space-y-3">
            {state.headers.map((entry, index) => (
              <div key={`header-${index}`} className="flex flex-col gap-3 sm:flex-row">
                <div className="sm:flex-1">
                  <label className="text-xs font-medium text-slate-500">Header</label>
                  <input
                    type="text"
                    className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:ring-blue-500"
                    value={entry.key}
                    onChange={(event) => handleHeaderChange(index, 'key', event.target.value)}
                  />
                </div>
                <div className="sm:flex-1">
                  <label className="text-xs font-medium text-slate-500">Value</label>
                  <input
                    type="text"
                    className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:ring-blue-500"
                    value={entry.value}
                    onChange={(event) => handleHeaderChange(index, 'value', event.target.value)}
                  />
                </div>
                <div className="sm:w-auto sm:self-end">
                  <button
                    type="button"
                    className="rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-600 hover:bg-slate-50"
                    onClick={() => removeHeaderRow(index)}
                  >
                    Remove
                  </button>
                </div>
              </div>
            ))}
          </div>
          {getFieldErrors('headers', errorResponse).map((error) => (
            <p key={error} className="text-xs text-red-600">
              {error}
            </p>
          ))}
        </div>

        <div className="space-y-3">
          <label className="block text-sm font-semibold text-slate-700">OAuth Connection</label>
          <div className="rounded-xl border border-indigo-100 bg-indigo-50 px-4 py-3 text-sm text-indigo-900">
            {mode === 'create' && <p>Save this MCP server first, then return to connect via OAuth.</p>}
            {mode === 'edit' && state.authMethod !== 'oauth2' && <p>Select OAuth 2.0 to enable this integration.</p>}
            {mode === 'edit' && state.authMethod === 'oauth2' && (
              <div className="space-y-1">
                <p>Status: <span className="font-semibold">{statusLabel(oauthStore.status)}</span></p>
                {oauthStore.scope && <p>Scope: <span className="font-mono">{oauthStore.scope}</span></p>}
                {oauthStore.expiresAt && <p>Token expires at {oauthStore.expiresAt}</p>}
              </div>
            )}
          </div>

          {mode === 'edit' && state.authMethod === 'oauth2' && (
            <div className="space-y-4 rounded-lg border border-slate-200 bg-white px-4 py-4">
              <div className="flex flex-col gap-2 rounded-lg border border-slate-200 bg-slate-50 px-3 py-3">
                <label className="inline-flex items-start gap-2 text-sm font-medium text-slate-700">
                  <input
                    type="checkbox"
                    className="mt-0.5 rounded border-slate-300 text-indigo-600 focus:ring-indigo-500 disabled:cursor-not-allowed disabled:opacity-70"
                    checked={useCustomClient}
                    onChange={(event) => setUseCustomClient(event.target.checked)}
                    disabled={oauthStore.requiresManualClient}
                  />
                  Use custom OAuth credentials
                </label>
                <p className="text-xs text-slate-500">
                  Provide an OAuth client ID + secret from your own app. Leave unchecked to let Gobii register a temporary
                  client automatically.
                  {oauthStore.requiresManualClient && ' This server requires manual credentials.'}
                </p>
              </div>
              {useCustomClient && (
                <div className="grid gap-3 sm:grid-cols-2">
                  <div>
                    <label className="text-xs font-medium text-slate-600" htmlFor="clientId">
                      OAuth Client ID
                    </label>
                    <input
                      id="clientId"
                      type="text"
                      className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-indigo-500 focus:ring-indigo-500"
                      value={clientId}
                      onChange={(event) => setClientId(event.target.value)}
                    />
                  </div>
                  <div>
                    <label className="text-xs font-medium text-slate-600" htmlFor="clientSecret">
                      OAuth Client Secret
                    </label>
                    <input
                      id="clientSecret"
                      type="password"
                      className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-indigo-500 focus:ring-indigo-500"
                      value={clientSecret}
                      onChange={(event) => setClientSecret(event.target.value)}
                    />
                  </div>
                </div>
              )}
              <div>
                <label className="text-xs font-medium text-slate-600">Scopes</label>
                <input
                  type="text"
                  className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-indigo-500 focus:ring-indigo-500"
                  placeholder="e.g. openid profile email"
                  value={oauthScope}
                  onChange={(event) => setOauthScope(event.target.value)}
                />
                <p className="text-xs text-slate-500">Separate scopes with spaces. Leave blank for defaults.</p>
              </div>
              {oauthStore.error && <p className="text-xs text-amber-600">{oauthStore.error}</p>}
              <div className="flex flex-wrap gap-3">
                <button
                  type="button"
                  className="inline-flex items-center rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700 disabled:opacity-60"
                  disabled={oauthStore.connecting || !server}
                  onClick={() =>
                    oauthStore.startOAuth({
                      clientId: useCustomClient ? clientId : undefined,
                      clientSecret: useCustomClient ? clientSecret : undefined,
                      scope: oauthScope.trim() || undefined,
                    })
                  }
                >
                  {oauthStore.connecting ? 'Starting…' : 'Connect with OAuth 2.0'}
                </button>
                <button
                  type="button"
                  className="inline-flex items-center rounded-lg border border-slate-200 px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-60"
                  disabled={oauthStore.revoking || oauthStore.status !== 'connected'}
                  onClick={() => oauthStore.revokeOAuth()}
                >
                  {oauthStore.revoking ? 'Revoking…' : 'Disconnect'}
                </button>
              </div>
            </div>
          )}
        </div>
      </form>
    </Modal>
  )
}

function getInitialState(server?: McpServerDetail): FormState {
  if (!server) {
    return {
      displayName: '',
      slug: '',
      url: '',
      isActive: true,
      authMethod: 'none',
      headers: createBlankHeaders(),
    }
  }
  return {
    displayName: server.displayName,
    slug: server.name,
    url: server.url,
    isActive: server.isActive,
    authMethod: server.authMethod,
    headers: headersFromObject(server.headers),
  }
}

function headersFromObject(headers: Record<string, string>): HeaderEntry[] {
  const entries = Object.entries(headers || {}).map(([key, value]) => ({ key, value }))
  return entries.length ? entries : createBlankHeaders()
}

function headersToObject(entries: HeaderEntry[]): Record<string, string> {
  const result: Record<string, string> = {}
  entries.forEach(({ key, value }) => {
    const trimmed = key.trim()
    if (trimmed) {
      result[trimmed] = value
    }
  })
  return result
}

function slugify(value: string): string {
  const normalized = value.normalize('NFKD').replace(/[\u0300-\u036f]/g, '')
  return normalized
    .toLowerCase()
    .replace(/[^a-z0-9\s-]/g, '')
    .trim()
    .replace(/[\s_-]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 64)
}

function getFieldErrors(field: string, errors?: FormErrors | null): string[] {
  if (!errors) {
    return []
  }
  return errors[field] || errors[toSnakeCase(field)] || []
}

function toSnakeCase(value: string): string {
  return value.replace(/[A-Z]/g, (char) => `_${char.toLowerCase()}`)
}

function statusLabel(status: OAuthStatus): string {
  switch (status) {
    case 'connected':
      return 'Connected'
    case 'pending':
      return 'Pending authorization'
    case 'loading':
      return 'Checking…'
    case 'disconnected':
      return 'Disconnected'
    default:
      return 'Idle'
  }
}

type OAuthStatus = ReturnType<typeof useMcpOAuth>['status']
