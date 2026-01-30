import type { Dispatch, FormEvent, SetStateAction } from 'react'
import { Fragment, useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ChevronDown, Loader2, ServerCog } from 'lucide-react'

import {
  createMcpServer,
  fetchMcpServerDetail,
  updateMcpServer,
  type McpServerDetail,
  type McpServerPayload,
} from '../../api/mcp'
import { HttpError } from '../../api/http'
import { useMcpOAuth } from '../../hooks/useMcpOAuth'
import { Modal } from '../common/Modal'

type HeaderEntry = { key: string; value: string }
type EnvEntry = { key: string; value: string }

type FormErrors = Record<string, string[]>

type McpServerFormModalProps = {
  mode: 'create' | 'edit'
  listUrl: string
  detailUrl?: string
  ownerScope?: string
  allowCommands?: boolean
  onClose: () => void
  onSuccess: (message: string) => void
  onError: (message: string) => void
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
  connectionType: 'http' | 'stdio'
  commandLine: string
  environmentEntries: EnvEntry[]
  isActive: boolean
  authMethod: string
  headers: HeaderEntry[]
  bearerToken: string
}

const BLANK_HEADER: HeaderEntry = { key: '', value: '' }
const BLANK_ENV: EnvEntry = { key: '', value: '' }

const createBlankHeaders = (): HeaderEntry[] => [{ ...BLANK_HEADER }]
const createBlankEnvEntries = (): EnvEntry[] => [{ ...BLANK_ENV }]

export function McpServerFormModal({
  mode,
  listUrl,
  detailUrl,
  ownerScope,
  allowCommands = false,
  onClose,
  onSuccess,
  onError,
  oauth,
}: McpServerFormModalProps) {
  const [state, setState] = useState<FormState>(() => getInitialState(undefined, allowCommands))
  const [formErrors, setFormErrors] = useState<FormErrors | null>(null)
  const [statusMessage, setStatusMessage] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [clientId, setClientId] = useState('')
  const [clientSecret, setClientSecret] = useState('')
  const [oauthScope, setOauthScope] = useState('')
  const [useCustomClient, setUseCustomClient] = useState(false)
  const [headersExpanded, setHeadersExpanded] = useState(() => hasConfiguredHeaders(getInitialState(undefined, allowCommands)))

  const shouldFetchDetail = mode === 'edit' && Boolean(detailUrl)
  const detailQuery = useQuery({
    queryKey: ['mcp-server-detail', detailUrl],
    queryFn: () => fetchMcpServerDetail(detailUrl!),
    enabled: shouldFetchDetail,
  })

  const server = detailQuery.data

  useEffect(() => {
    if (mode === 'edit' && server) {
      const nextState = getInitialState(server, allowCommands)
      setState(nextState)
      setHeadersExpanded(hasConfiguredHeaders(nextState))
    }
  }, [mode, server, allowCommands])

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
    if (oauthStore.requiresManualClient) {
      setUseCustomClient(true)
    }
  }, [oauthStore.requiresManualClient])

  useEffect(() => {
    if (getFieldErrors('headers', formErrors).length > 0) {
      setHeadersExpanded(true)
    }
  }, [formErrors])

  const nonFieldErrors = formErrors?.non_field_errors ?? []

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault()
    const isStdio = allowCommands && state.connectionType === 'stdio'
    let command = ''
    let commandArgs: string[] = []
    let environment: Record<string, string> = {}

    if (isStdio) {
      const parseResult = parseCommandLine(state.commandLine)
      if (parseResult.error) {
        setStatusMessage(parseResult.error)
        onError(parseResult.error)
        return
      }
      command = parseResult.command
      commandArgs = parseResult.args
      environment = entriesToObject(state.environmentEntries)
    } else if (!state.url.trim()) {
      const message = 'URL is required for HTTP connections.'
      setStatusMessage(message)
      onError(message)
      return
    }

    const payload: McpServerPayload = {
      display_name: state.displayName.trim(),
      name: state.slug.trim(),
      url: isStdio ? '' : state.url.trim(),
      auth_method: isStdio ? 'none' : state.authMethod,
      is_active: state.isActive,
      headers: isStdio
        ? {}
        : headersToObject(state.headers, {
            authMethod: state.authMethod,
            bearerToken: state.bearerToken,
          }),
      metadata: {},
      environment,
      command,
      command_args: commandArgs,
    }

    setFormErrors(null)
    setStatusMessage(null)
    setIsSubmitting(true)

    try {
      if (mode === 'create') {
        await createMcpServer(listUrl, payload)
        onSuccess('MCP server saved.')
      } else if (detailUrl) {
        await updateMcpServer(detailUrl, payload)
        onSuccess('MCP server updated.')
      } else {
        throw new Error('Missing detail URL for edit modal.')
      }
    } catch (error) {
      if (error instanceof HttpError && error.status === 400 && isErrorBody(error.body)) {
        setFormErrors(error.body.errors || null)
        if (error.body.message) {
          setStatusMessage(error.body.message)
          onError(error.body.message)
        }
      } else {
        const fallback = mode === 'create' ? 'Unable to save MCP server.' : 'Unable to update MCP server.'
        const message = resolveErrorMessage(error, fallback)
        setStatusMessage(message)
        onError(message)
      }
      return
    } finally {
      setIsSubmitting(false)
    }

    onClose()
  }

  const formTitle =
    mode === 'create' ? 'Add MCP Server' : `Edit ${server?.displayName ?? 'MCP Server'}`
  const ownerLabelText = ownerScope === 'organization' ? 'your organization' : 'your workspace'
  const modalSubtitle =
    mode === 'create'
      ? `Connect a new MCP integration for ${ownerLabelText}.`
      : `Update connection and OAuth settings for ${server?.displayName ?? 'this MCP server'}.`

  if (mode === 'edit' && detailQuery.isLoading) {
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

  if (mode === 'edit' && detailQuery.error) {
    const message = resolveErrorMessage(detailQuery.error, 'Failed to load MCP server details.')
    return (
      <Modal
        title={formTitle}
        subtitle={modalSubtitle}
        onClose={onClose}
        icon={Loader2}
        iconBgClass="bg-red-100"
        iconColorClass="text-red-600"
      >
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {message}
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
        disabled={isSubmitting}
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
      <form id="mcp-server-form" className="space-y-6 p-1" onSubmit={handleSubmit}>
        {(statusMessage || nonFieldErrors.length > 0) && (
          <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 space-y-1">
            {statusMessage && <p>{statusMessage}</p>}
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
              onChange={(event) => {
                const value = event.target.value
                setState((prev) => ({
                  ...prev,
                  displayName: value,
                  slug: mode === 'create' ? slugify(value) : prev.slug,
                }))
              }}
              required
            />
            <p className="text-xs text-slate-500">
              Identifier: <span className="font-mono text-slate-700">{state.slug || 'auto-generated'}</span>
            </p>
            {getFieldErrors('display_name', formErrors).map((error) => (
              <p key={error} className="text-xs text-red-600">
                {error}
              </p>
            ))}
            {getFieldErrors('name', formErrors).map((error) => (
              <p key={error} className="text-xs text-red-600">
                {error}
              </p>
            ))}
          </div>

          <div>
            <label className="block text-sm font-medium text-slate-700">Connection Type</label>
            <div className="mt-1 flex flex-col gap-3 sm:flex-row">
              <select
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:ring-blue-500 sm:w-40"
                value={state.connectionType}
                onChange={(event) =>
                  setState((prev) => {
                    const nextType = event.target.value as FormState['connectionType']
                    return {
                      ...prev,
                      connectionType: nextType,
                      authMethod: nextType === 'stdio' ? 'none' : prev.authMethod,
                    }
                  })
                }
              >
                <option value="http">HTTP</option>
                <option value="stdio" disabled={!allowCommands}>
                  STDIO
                </option>
              </select>
              <div className="flex-1">
                {state.connectionType === 'http' ? (
                  <input
                    type="url"
                    className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:ring-blue-500"
                    value={state.url}
                    onChange={(event) => setState((prev) => ({ ...prev, url: event.target.value }))}
                    required
                    placeholder="https://mcp.example.com"
                  />
                ) : (
                  <input
                    type="text"
                    className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:ring-blue-500"
                    value={state.commandLine}
                    onChange={(event) => setState((prev) => ({ ...prev, commandLine: event.target.value }))}
                    placeholder="e.g. npx -y @my/mcp@1.0.0"
                  />
                )}
              </div>
            </div>
            {getFieldErrors('url', formErrors).map((error) => (
              <p key={error} className="text-xs text-red-600">
                {error}
              </p>
            ))}
            {state.connectionType === 'stdio' &&
              getFieldErrors('command', formErrors).map((error) => (
                <p key={error} className="text-xs text-red-600">
                  {error}
                </p>
              ))}
            {state.connectionType === 'stdio' &&
              getFieldErrors('command_args', formErrors).map((error) => (
                <p key={error} className="text-xs text-red-600">
                  {error}
                </p>
              ))}
          </div>

          {allowCommands && state.connectionType === 'stdio' && (
            <div className="rounded-lg border border-slate-200 bg-white px-4 py-4 space-y-4">
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <label className="text-xs font-medium text-slate-600">Environment variables</label>
                  <button
                    type="button"
                    className="text-xs font-medium text-blue-600 hover:text-blue-700"
                    onClick={() =>
                      setState((prev) => ({ ...prev, environmentEntries: [...prev.environmentEntries, { ...BLANK_ENV }] }))
                    }
                  >
                    Add variable
                  </button>
                </div>
                <div className="space-y-3">
                  {state.environmentEntries.map((entry, index) => (
                    <div key={`env-${index}`} className="flex flex-col gap-3 sm:flex-row">
                      <div className="sm:flex-1">
                        <label className="text-xs font-medium text-slate-500">Key</label>
                        <input
                          type="text"
                          className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:ring-blue-500"
                          value={entry.key}
                          onChange={(event) => handleEnvChange(index, 'key', event.target.value, setState)}
                          placeholder="API_KEY"
                        />
                      </div>
                      <div className="sm:flex-1">
                        <label className="text-xs font-medium text-slate-500">Value</label>
                        <input
                          type="text"
                          className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:ring-blue-500"
                          value={entry.value}
                          onChange={(event) => handleEnvChange(index, 'value', event.target.value, setState)}
                        />
                      </div>
                      <div className="sm:w-auto sm:self-end">
                        <button
                          type="button"
                          className="rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-600 hover:bg-slate-50"
                          onClick={() =>
                            setState((prev) => {
                              const entries = prev.environmentEntries.filter((_, idx) => idx !== index)
                              return { ...prev, environmentEntries: entries.length ? entries : createBlankEnvEntries() }
                            })
                          }
                        >
                          Remove
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
                {getFieldErrors('environment', formErrors).map((error) => (
                  <p key={error} className="text-xs text-red-600">
                    {error}
                  </p>
                ))}
              </div>
            </div>
          )}

          {state.connectionType !== 'stdio' && (
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
              {getFieldErrors('auth_method', formErrors).map((error) => (
                <p key={error} className="text-xs text-red-600">
                  {error}
                </p>
              ))}
            </div>
          )}

          {state.connectionType !== 'stdio' && state.authMethod === 'bearer_token' && (
            <div>
              <label className="block text-sm font-medium text-slate-700">Bearer Token</label>
              <input
                type="password"
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:ring-blue-500"
                value={state.bearerToken}
                onChange={(event) => setState((prev) => ({ ...prev, bearerToken: event.target.value }))}
                autoComplete="off"
                placeholder="Enter secure token"
              />
              <p className="text-xs text-slate-500">Stored securely and sent as the Authorization header.</p>
            </div>
          )}

          {ownerScope === 'organization' && (
            <div className="rounded-lg border border-amber-100 bg-amber-50 px-4 py-3 text-xs text-amber-800">
              Only members with manage permissions can update organization servers.
            </div>
          )}
        </div>

        {state.connectionType !== 'stdio' && state.authMethod === 'oauth2' && (
          <div className="space-y-3">
            <label className="block text-sm font-semibold text-slate-700">OAuth Connection</label>
            <div className="rounded-xl border border-indigo-100 bg-indigo-50 px-4 py-3 text-sm text-indigo-900">
              {mode === 'create' ? (
                <p>Save this MCP server first, then return to connect via OAuth.</p>
              ) : (
                <div className="space-y-1">
                  <p>
                    Status: <span className="font-semibold">{statusLabel(oauthStore.status)}</span>
                  </p>
                  {oauthStore.scope && (
                    <p>
                      Scope: <span className="font-mono">{oauthStore.scope}</span>
                    </p>
                  )}
                </div>
              )}
            </div>

            {mode === 'edit' && (
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
        )}

        {state.connectionType !== 'stdio' && (
          <div className="rounded-lg border border-slate-200 bg-white">
            <button
              type="button"
              className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left"
              onClick={() => setHeadersExpanded((prev) => !prev)}
              aria-expanded={headersExpanded}
            >
              <div>
                <p className="text-sm font-semibold text-slate-700 capitalize">Custom headers</p>
                <p className="text-xs text-slate-500">Encrypted and stored securely.</p>
              </div>
              <div className="flex items-center gap-3">
                <span className="inline-flex items-center rounded-full border border-slate-200 px-2.5 py-0.5 text-xs font-medium text-slate-600">
                  Optional
                </span>
                {hasConfiguredHeaders(state) && !headersExpanded && (
                  <span className="text-xs font-medium text-slate-600">Configured</span>
                )}
                <ChevronDown
                  className={`h-4 w-4 text-slate-500 transition-transform ${headersExpanded ? 'rotate-180' : ''}`}
                  aria-hidden="true"
                />
              </div>
            </button>
            {headersExpanded && (
              <div className="space-y-4 border-t border-slate-100 px-4 py-4">
                <div className="flex items-center justify-between">
                  <p className="text-sm font-medium text-slate-700">Header entries</p>
                  <button
                    type="button"
                    className="text-sm font-medium text-blue-600 hover:text-blue-700"
                    onClick={() => setState((prev) => ({ ...prev, headers: [...prev.headers, { ...BLANK_HEADER }] }))}
                  >
                    Add Header
                  </button>
                </div>
                <div className="space-y-3">
                  {state.headers.map((entry, index) => (
                    <div key={`header-${index}`} className="flex flex-col gap-3 sm:flex-row">
                      <div className="sm:flex-1">
                        <label className="text-xs font-medium text-slate-500">Header</label>
                        <input
                          type="text"
                          className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:ring-blue-500"
                          value={entry.key}
                          onChange={(event) => handleHeaderChange(index, 'key', event.target.value, setState)}
                        />
                      </div>
                      <div className="sm:flex-1">
                        <label className="text-xs font-medium text-slate-500">Value</label>
                        <input
                          type="text"
                          className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:ring-blue-500"
                          value={entry.value}
                          onChange={(event) => handleHeaderChange(index, 'value', event.target.value, setState)}
                        />
                      </div>
                      <div className="sm:w-auto sm:self-end">
                        <button
                          type="button"
                          className="rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-600 hover:bg-slate-50"
                          onClick={() =>
                            setState((prev) => {
                              const headers = prev.headers.filter((_, idx) => idx !== index)
                              return { ...prev, headers: headers.length ? headers : createBlankHeaders() }
                            })
                          }
                        >
                          Remove
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
                {getFieldErrors('headers', formErrors).map((error) => (
                  <p key={error} className="text-xs text-red-600">
                    {error}
                  </p>
                ))}
              </div>
            )}
          </div>
        )}
      </form>
    </Modal>
  )
}

function getInitialState(server?: McpServerDetail, allowCommands = false): FormState {
  if (!server) {
    return {
      displayName: '',
      slug: '',
      url: '',
      connectionType: 'http',
      commandLine: '',
      environmentEntries: createBlankEnvEntries(),
      isActive: true,
      authMethod: 'none',
      headers: createBlankHeaders(),
      bearerToken: '',
    }
  }
  const { headerEntries, bearerToken } = splitHeaders(server.headers, server.authMethod)
  const hasCommand = Boolean(server.command)
  const connectionType = allowCommands && hasCommand ? 'stdio' : 'http'
  return {
    displayName: server.displayName,
    slug: server.name,
    url: server.url,
    connectionType,
    commandLine: hasCommand ? formatCommandLine(server.command ?? '', server.commandArgs ?? []) : '',
    environmentEntries: environmentToEntries(server.environment),
    isActive: server.isActive,
    authMethod: server.authMethod,
    headers: headerEntries,
    bearerToken,
  }
}

function splitHeaders(headers: Record<string, string>, authMethod: string) {
  const pairs = Object.entries(headers || {})
  let bearerToken = ''
  const headerEntries: HeaderEntry[] = []

  pairs.forEach(([key, value]) => {
    if (authMethod === 'bearer_token' && key.toLowerCase() === 'authorization') {
      const token = extractBearerToken(value)
      if (token) {
        bearerToken = token
        return
      }
    }
    headerEntries.push({ key, value })
  })

  return {
    headerEntries: headerEntries.length ? headerEntries : createBlankHeaders(),
    bearerToken,
  }
}

function headersToObject(
  entries: HeaderEntry[],
  options: { authMethod?: string; bearerToken?: string } = {},
): Record<string, string> {
  const result: Record<string, string> = {}
  entries.forEach(({ key, value }) => {
    const trimmed = key.trim()
    if (trimmed) {
      result[trimmed] = value
    }
  })
  if (options.authMethod === 'bearer_token') {
    Object.keys(result).forEach((key) => {
      if (key.toLowerCase() === 'authorization') {
        delete result[key]
      }
    })
    const token = options.bearerToken?.trim()
    if (token) {
      result.Authorization = `Bearer ${token}`
    }
  }
  return result
}

function handleHeaderChange(
  index: number,
  key: 'key' | 'value',
  value: string,
  setState: Dispatch<SetStateAction<FormState>>,
) {
  setState((prev) => {
    const headers = [...prev.headers]
    headers[index] = { ...headers[index], [key]: value }
    return { ...prev, headers }
  })
}

function handleEnvChange(
  index: number,
  key: 'key' | 'value',
  value: string,
  setState: Dispatch<SetStateAction<FormState>>,
) {
  setState((prev) => {
    const entries = [...prev.environmentEntries]
    entries[index] = { ...entries[index], [key]: value }
    return { ...prev, environmentEntries: entries }
  })
}

function entriesToObject(entries: EnvEntry[]): Record<string, string> {
  const result: Record<string, string> = {}
  entries.forEach(({ key, value }) => {
    const trimmed = key.trim()
    if (trimmed) {
      result[trimmed] = value
    }
  })
  return result
}

function environmentToEntries(environment?: Record<string, string> | null): EnvEntry[] {
  if (!environment || Object.keys(environment).length === 0) {
    return createBlankEnvEntries()
  }
  return Object.entries(environment).map(([key, value]) => ({ key, value }))
}

function formatCommandLine(command: string, args: string[]): string {
  return [command, ...args].filter(Boolean).map(quoteCommandArg).join(' ')
}

function quoteCommandArg(value: string): string {
  if (!value) {
    return ''
  }
  if (!/[\s"'\\]/.test(value)) {
    return value
  }
  const escaped = value.replace(/["\\]/g, '\\$&')
  return `"${escaped}"`
}

function parseCommandLine(raw: string): { command: string; args: string[]; error?: string } {
  const input = raw.trim()
  if (!input) {
    return { command: '', args: [], error: 'Command is required for STDIO connections.' }
  }
  const tokens: string[] = []
  let current = ''
  let quote: '"' | "'" | null = null
  for (let i = 0; i < input.length; i += 1) {
    const char = input[i]
    if (quote) {
      if (char === quote) {
        quote = null
      } else if (char === '\\' && quote === '"' && i + 1 < input.length) {
        current += input[i + 1]
        i += 1
      } else {
        current += char
      }
      continue
    }
    if (char === '"' || char === "'") {
      quote = char
      continue
    }
    if (/\s/.test(char)) {
      if (current) {
        tokens.push(current)
        current = ''
      }
      continue
    }
    if (char === '\\' && i + 1 < input.length) {
      current += input[i + 1]
      i += 1
      continue
    }
    current += char
  }
  if (quote) {
    return { command: '', args: [], error: 'Command contains an unterminated quote.' }
  }
  if (current) {
    tokens.push(current)
  }
  if (!tokens.length) {
    return { command: '', args: [], error: 'Command is required for STDIO connections.' }
  }
  const [command, ...args] = tokens
  return { command, args }
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

function isErrorBody(payload: unknown): payload is { errors?: FormErrors; message?: string } {
  if (!payload || typeof payload !== 'object') {
    return false
  }
  const record = payload as Record<string, unknown>
  return 'errors' in record || 'message' in record
}

function resolveErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof HttpError) {
    if (typeof error.body === 'string' && error.body) {
      return error.body
    }
    if (typeof error.statusText === 'string' && error.statusText) {
      return error.statusText
    }
  }
  if (error && typeof error === 'object' && 'message' in error && typeof (error as { message: unknown }).message === 'string') {
    return (error as { message: string }).message
  }
  return fallback
}

function extractBearerToken(value: string): string | null {
  if (!value) {
    return null
  }
  const match = value.match(/^Bearer\s+(.+)$/i)
  return match ? match[1].trim() : null
}

function hasCustomHeaderEntries(entries: HeaderEntry[]): boolean {
  return entries.some(({ key, value }) => key.trim() || value.trim())
}

function hasConfiguredHeaders(state: FormState): boolean {
  const hasBearerToken = state.authMethod === 'bearer_token' && Boolean(state.bearerToken.trim())
  return hasCustomHeaderEntries(state.headers) || hasBearerToken
}
