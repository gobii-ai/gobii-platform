import { useCallback, useMemo, useState, type FormEvent } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Ban, Check, Clipboard, KeyRound, Plus, ShieldAlert, Trash2, type LucideIcon } from 'lucide-react'

import {
  createApiKey,
  deleteApiKey,
  fetchApiKeys,
  revokeApiKey,
  type ApiKeyDTO,
} from '../api/apiKeys'
import { HttpError } from '../api/http'
import { SettingsBanner } from '../components/agentSettings/SettingsBanner'
import { embeddedSettingsSurfaceClassName, sharedSettingsGlassFrameClassName } from '../components/agentSettings/settingsSurfaceClasses'
import { Modal } from '../components/common/Modal'
import { ModalForm } from '../components/common/ModalForm'
import { useModal } from '../hooks/useModal'

type CreatedKeyState = {
  name: string
  rawKey: string
}

const dateFormatter = new Intl.DateTimeFormat('en-US', {
  month: 'short',
  day: 'numeric',
  year: 'numeric',
})

function formatDate(value: string | null): string {
  if (!value) {
    return 'Never'
  }
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }
  return dateFormatter.format(date)
}

function formatErrors(error: unknown, fallback: string): string[] {
  if (error instanceof HttpError && typeof error.body === 'object' && error.body) {
    const body = error.body as Record<string, unknown>
    if (body.errors && typeof body.errors === 'object') {
      return Object.values(body.errors as Record<string, unknown>).flatMap((messages) => (
        Array.isArray(messages) ? messages.map(String) : [String(messages)]
      ))
    }
    if (body.error) {
      return [String(body.error)]
    }
  }
  if (error instanceof Error) {
    return [error.message]
  }
  return [fallback]
}

function CreateApiKeyModal({
  onClose,
  onCreated,
}: {
  onClose: () => void
  onCreated: (created: CreatedKeyState) => void
}) {
  const [name, setName] = useState('New API Key')
  const [busy, setBusy] = useState(false)
  const [errors, setErrors] = useState<string[]>([])

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setErrors([])
    try {
      const payload = await createApiKey(name.trim())
      onCreated({ name: payload.api_key.name, rawKey: payload.raw_key })
      onClose()
    } catch (error) {
      setErrors(formatErrors(error, 'Unable to create API key.'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <ModalForm
      id="api-key-form"
      title="Create API Key"
      subtitle="Name this key so you can identify where it is used."
      onClose={onClose}
      onSubmit={handleSubmit}
      widthClass="sm:max-w-lg"
      icon={KeyRound}
      iconBgClass="bg-blue-100"
      iconColorClass="text-blue-600"
      submitLabel="Create Key"
      submittingLabel="Creating..."
      submitting={busy}
      errorMessages={errors}
    >
        <div>
          <label htmlFor="api-key-name" className="block text-sm font-medium text-slate-700">
            Key Name
          </label>
          <input
            id="api-key-name"
            type="text"
            required
            value={name}
            onChange={(event) => setName(event.target.value)}
            className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:ring-blue-500"
            autoFocus
            onFocus={(event) => event.currentTarget.select()}
          />
        </div>
    </ModalForm>
  )
}

function CreatedApiKeyModal({
  created,
  onClose,
}: {
  created: CreatedKeyState
  onClose: () => void
}) {
  const [copied, setCopied] = useState(false)

  const handleCopy = async () => {
    await navigator.clipboard?.writeText(created.rawKey)
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1500)
  }

  const footer = (
    <button
      type="button"
      className="inline-flex w-full justify-center rounded-md border border-transparent bg-blue-600 px-4 py-2 text-base font-medium text-white shadow-sm transition hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 sm:w-auto sm:text-sm"
      onClick={onClose}
    >
      Done
    </button>
  )

  return (
    <Modal
      title="API Key Created"
      subtitle={`Copy "${created.name}" now. It will not be shown again.`}
      onClose={onClose}
      footer={footer}
      widthClass="sm:max-w-xl"
      icon={KeyRound}
      iconBgClass="bg-green-100"
      iconColorClass="text-green-600"
      dismissible={false}
    >
      <div className="space-y-4">
        <div className="rounded-lg border border-slate-200 bg-slate-950 p-3">
          <code className="block break-all font-mono text-sm text-slate-100">{created.rawKey}</code>
        </div>
        <button
          type="button"
          onClick={handleCopy}
          className="inline-flex items-center gap-2 rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-sm font-medium text-blue-700 transition hover:bg-blue-100"
        >
          {copied ? <Check className="h-4 w-4" /> : <Clipboard className="h-4 w-4" />}
          {copied ? 'Copied' : 'Copy Key'}
        </button>
      </div>
    </Modal>
  )
}

function ConfirmApiKeyActionModal({
  title,
  subtitle,
  confirmLabel,
  icon,
  onClose,
  onConfirm,
}: {
  title: string
  subtitle: string
  confirmLabel: string
  icon: LucideIcon
  onClose: () => void
  onConfirm: () => Promise<void>
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleConfirm = async () => {
    setBusy(true)
    setError(null)
    try {
      await onConfirm()
      onClose()
    } catch (err) {
      setError(formatErrors(err, 'Unable to update API key.')[0] ?? 'Unable to update API key.')
    } finally {
      setBusy(false)
    }
  }

  const footer = (
    <>
      <button
        type="button"
        className="inline-flex w-full justify-center rounded-md border border-transparent bg-red-600 px-4 py-2 text-base font-medium text-white shadow-sm transition hover:bg-red-700 focus:outline-none focus:ring-2 focus:ring-red-500 focus:ring-offset-2 disabled:opacity-60 sm:ml-3 sm:w-auto sm:text-sm"
        onClick={handleConfirm}
        disabled={busy}
      >
        {busy ? 'Working...' : confirmLabel}
      </button>
      <button
        type="button"
        className="inline-flex w-full justify-center rounded-md border border-slate-300 bg-white px-4 py-2 text-base font-medium text-slate-700 shadow-sm transition hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 disabled:opacity-60 sm:ml-3 sm:w-auto sm:text-sm"
        onClick={onClose}
        disabled={busy}
      >
        Cancel
      </button>
    </>
  )

  return (
    <Modal
      title={title}
      subtitle={subtitle}
      onClose={onClose}
      footer={footer}
      widthClass="sm:max-w-lg"
      icon={icon}
      iconBgClass="bg-red-100"
      iconColorClass="text-red-600"
    >
      {error ? <p className="text-sm text-red-600">{error}</p> : <p className="text-sm text-slate-600">This change takes effect immediately.</p>}
    </Modal>
  )
}

export function ApiKeysScreen() {
  const queryClient = useQueryClient()
  const queryKey = useMemo(() => ['api-keys'] as const, [])
  const [modal, showModal] = useModal()
  const [createdKey, setCreatedKey] = useState<CreatedKeyState | null>(null)
  const [banner, setBanner] = useState<string | null>(null)
  const { data, isLoading, error } = useQuery({
    queryKey,
    queryFn: ({ signal }) => fetchApiKeys(signal),
  })

  const keys = data?.api_keys ?? []
  const canManage = Boolean(data?.can_manage)
  const emailVerified = data?.email_verified !== false
  const ownerScope = data?.owner_scope ?? 'user'
  const ownerName = data?.owner_name ?? 'Personal workspace'
  const subtitle = ownerScope === 'organization'
    ? `These keys authenticate API requests for ${ownerName}.`
    : 'Create and manage API credentials for your account.'
  const listError = error instanceof Error ? error.message : null

  const refresh = useCallback(() => {
    queryClient.invalidateQueries({ queryKey })
  }, [queryClient, queryKey])

  const handleCreated = useCallback((created: CreatedKeyState) => {
    setCreatedKey(created)
    setBanner('API key created.')
    refresh()
  }, [refresh])

  const openCreateModal = useCallback(() => {
    showModal((onClose) => (
      <CreateApiKeyModal
        onClose={onClose}
        onCreated={handleCreated}
      />
    ))
  }, [handleCreated, showModal])

  const openRevokeModal = useCallback((apiKey: ApiKeyDTO) => {
    showModal((onClose) => (
      <ConfirmApiKeyActionModal
        title="Revoke API Key"
        subtitle={`Revoke "${apiKey.name}"? Existing clients using this key will stop authenticating.`}
        confirmLabel="Revoke Key"
        icon={Ban}
        onClose={onClose}
        onConfirm={async () => {
          await revokeApiKey(apiKey.id)
          setBanner(`API key "${apiKey.name}" revoked.`)
          refresh()
        }}
      />
    ))
  }, [refresh, showModal])

  const openDeleteModal = useCallback((apiKey: ApiKeyDTO) => {
    showModal((onClose) => (
      <ConfirmApiKeyActionModal
        title="Delete API Key"
        subtitle={`Permanently delete "${apiKey.name}"? This cannot be undone.`}
        confirmLabel="Delete Key"
        icon={Trash2}
        onClose={onClose}
        onConfirm={async () => {
          await deleteApiKey(apiKey.id)
          setBanner(`API key "${apiKey.name}" deleted.`)
          refresh()
        }}
      />
    ))
  }, [refresh, showModal])

  const frameClassName = `${sharedSettingsGlassFrameClassName} ${embeddedSettingsSurfaceClassName} shadow-none`
  const tableClassName = 'min-w-full divide-y divide-slate-200/15'
  const tableHeadClassName = 'bg-slate-900/40'
  const tableBodyClassName = 'divide-y divide-slate-200/15'
  const rowClassName = 'hover:bg-slate-900/30'
  const headerTextClassName = 'px-6 py-3 text-left text-xs font-semibold uppercase text-slate-300'
  const cellTextClassName = 'px-6 py-4 text-sm text-slate-300'
  const actionClassName = 'inline-flex items-center gap-1 rounded border border-slate-300/70 bg-transparent px-2 py-1 text-xs font-medium text-slate-100 transition-colors hover:border-slate-200 hover:text-white disabled:opacity-50'
  const destructiveClassName = 'inline-flex items-center gap-1 rounded border border-rose-300/40 bg-rose-950/20 px-2 py-1 text-xs font-medium text-rose-100 transition-colors hover:border-rose-200 hover:bg-rose-900/30'

  return (
    <div className="space-y-6 pb-6">
      {modal}
      {createdKey ? <CreatedApiKeyModal created={createdKey} onClose={() => setCreatedKey(null)} /> : null}

      <SettingsBanner
        variant="embedded"
        eyebrow="Workspace"
        title="API Keys"
        subtitle={subtitle}
        actions={canManage && emailVerified ? (
          <button
            type="button"
            onClick={openCreateModal}
            className="inline-flex w-full items-center justify-center gap-x-2 rounded-lg border border-blue-300/40 bg-blue-950/20 px-4 py-2 text-sm font-medium text-blue-100 transition-colors hover:border-blue-200 hover:bg-blue-900/30 focus:outline-none sm:w-auto"
          >
            <Plus className="h-4 w-4" />
            Create New Key
          </button>
        ) : null}
      />

      {!emailVerified ? (
        <div className="rounded-lg border border-amber-300/30 bg-amber-950/20 px-4 py-3 text-sm text-amber-100">
          Please verify your email address to create API keys.
        </div>
      ) : null}

      {data && !canManage ? (
        <div className="flex items-center gap-2 rounded-lg border border-amber-300/30 bg-amber-950/20 px-4 py-3 text-sm text-amber-100">
          <ShieldAlert className="h-4 w-4 shrink-0" />
          <span>Read-only access. Contact an owner or admin to create or manage keys.</span>
        </div>
      ) : null}

      {banner ? (
        <div className="rounded-lg border border-green-300/30 bg-green-950/20 px-4 py-3 text-sm text-green-100">
          {banner}
        </div>
      ) : null}

      {listError ? (
        <div className="rounded-lg border border-red-300/30 bg-red-950/20 px-4 py-3 text-sm text-red-100">
          {listError}
        </div>
      ) : null}

      <div className={frameClassName}>
        {isLoading ? (
          <div className="flex justify-center py-12">
            <div className="h-8 w-8 animate-spin rounded-full border-4 border-blue-300/30 border-t-blue-200" />
          </div>
        ) : keys.length === 0 ? (
          <div className="p-8 text-center">
            <div className="mb-4 flex justify-center">
              <div className="flex h-12 w-12 items-center justify-center rounded-full border border-slate-300/70 bg-slate-900/40">
                <KeyRound className="h-6 w-6 text-slate-400" />
              </div>
            </div>
            <p className="text-sm text-slate-400">
              No API keys found.{canManage && emailVerified ? ' Create one to get started.' : ''}
            </p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className={tableClassName}>
              <thead className={tableHeadClassName}>
                <tr>
                  <th scope="col" className={headerTextClassName}>Name</th>
                  {ownerScope === 'organization' ? <th scope="col" className={headerTextClassName}>Created By</th> : null}
                  <th scope="col" className={headerTextClassName}>Created</th>
                  <th scope="col" className={headerTextClassName}>Last Used</th>
                  <th scope="col" className={headerTextClassName}>Status</th>
                  {canManage ? <th scope="col" className={`${headerTextClassName} text-right`}>Actions</th> : null}
                </tr>
              </thead>
              <tbody className={tableBodyClassName}>
                {keys.map((apiKey) => (
                  <tr key={apiKey.id} className={rowClassName}>
                    <td className={cellTextClassName}>
                      <div className="font-medium text-slate-100">{apiKey.name}</div>
                      <div className="mt-1 text-xs text-slate-400">Prefix: {apiKey.prefix}</div>
                    </td>
                    {ownerScope === 'organization' ? (
                      <td className={cellTextClassName}>{apiKey.created_by ?? '-'}</td>
                    ) : null}
                    <td className={cellTextClassName}>{formatDate(apiKey.created_at)}</td>
                    <td className={cellTextClassName}>{formatDate(apiKey.last_used_at)}</td>
                    <td className={cellTextClassName}>
                      <span className={apiKey.is_active
                        ? 'inline-flex rounded-full border border-green-300/30 bg-green-950/20 px-2 py-0.5 text-xs font-medium text-green-100'
                        : 'inline-flex rounded-full border border-red-300/30 bg-red-950/20 px-2 py-0.5 text-xs font-medium text-red-100'}
                      >
                        {apiKey.is_active ? 'Active' : 'Revoked'}
                      </span>
                    </td>
                    {canManage ? (
                      <td className={`${cellTextClassName} text-right`}>
                        <div className="flex items-center justify-end gap-1.5">
                          <button
                            type="button"
                            className={actionClassName}
                            disabled={!apiKey.is_active}
                            onClick={() => openRevokeModal(apiKey)}
                          >
                            <Ban className="h-3 w-3" />
                            Revoke
                          </button>
                          <button
                            type="button"
                            className={destructiveClassName}
                            onClick={() => openDeleteModal(apiKey)}
                          >
                            <Trash2 className="h-3 w-3" />
                            Delete
                          </button>
                        </div>
                      </td>
                    ) : null}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
