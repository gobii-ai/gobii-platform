import { useCallback, useMemo, useState, type FormEvent } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Ban, Check, Clipboard, KeyRound, Plus, ShieldAlert, Trash2 } from 'lucide-react'

import { createApiKey, deleteApiKey, fetchApiKeys, revokeApiKey, type ApiKeyDTO } from '../api/apiKeys'
import { apiErrorMessages } from '../api/safeErrorMessage'
import {
  EmbeddedTableActionButton,
  embeddedCompactDestructiveButtonClassName,
  embeddedDarkTableHeadClassName,
  embeddedSecondaryActionButtonClassName,
  embeddedDividedTableBodyClassName,
  embeddedTableCellClassName,
  embeddedTableClassName,
  embeddedTableHeaderCellClassName,
  embeddedTableRowClassName,
} from '../components/agentSettings/embeddedTablePrimitives'
import { SettingsBanner } from '../components/agentSettings/SettingsBanner'
import { AsyncActionConfirmDialog } from '../components/common/ActionConfirmDialog'
import { FormField, TextInput } from '../components/common/FormControls'
import { InlineStatusBanner } from '../components/common/InlineStatusBanner'
import { Modal } from '../components/common/Modal'
import { ModalForm } from '../components/common/ModalForm'
import { SettingsSurface } from '../components/common/SettingsSurface'
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
      setErrors(apiErrorMessages(error, 'Unable to create API key.'))
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
        <FormField id="api-key-name" label="Key Name">
          <TextInput
            id="api-key-name"
            type="text"
            required
            value={name}
            onChange={(event) => setName(event.target.value)}
            autoFocus
            onFocus={(event) => event.currentTarget.select()}
          />
        </FormField>
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
      <AsyncActionConfirmDialog
        open
        title="Revoke API Key"
        description={`Revoke "${apiKey.name}"? Existing clients using this key will stop authenticating.`}
        confirmLabel="Revoke Key"
        icon={Ban}
        onClose={onClose}
        onConfirm={async () => {
          await revokeApiKey(apiKey.id)
          setBanner(`API key "${apiKey.name}" revoked.`)
          refresh()
        }}
        danger
        widthClass="sm:max-w-lg"
        getErrorMessage={(err) => apiErrorMessages(err, 'Unable to update API key.')[0] ?? 'Unable to update API key.'}
      >
        <p className="text-sm text-slate-600">This change takes effect immediately.</p>
      </AsyncActionConfirmDialog>
    ))
  }, [refresh, showModal])

  const openDeleteModal = useCallback((apiKey: ApiKeyDTO) => {
    showModal((onClose) => (
      <AsyncActionConfirmDialog
        open
        title="Delete API Key"
        description={`Permanently delete "${apiKey.name}"? This cannot be undone.`}
        confirmLabel="Delete Key"
        icon={Trash2}
        onClose={onClose}
        onConfirm={async () => {
          await deleteApiKey(apiKey.id)
          setBanner(`API key "${apiKey.name}" deleted.`)
          refresh()
        }}
        danger
        widthClass="sm:max-w-lg"
        getErrorMessage={(err) => apiErrorMessages(err, 'Unable to update API key.')[0] ?? 'Unable to update API key.'}
      >
        <p className="text-sm text-slate-600">This change takes effect immediately.</p>
      </AsyncActionConfirmDialog>
    ))
  }, [refresh, showModal])

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
        <InlineStatusBanner variant="warning" surface="embedded">
          Please verify your email address to create API keys.
        </InlineStatusBanner>
      ) : null}

      {data && !canManage ? (
        <InlineStatusBanner variant="warning" surface="embedded" icon={ShieldAlert}>
          <span>Read-only access. Contact an owner or admin to create or manage keys.</span>
        </InlineStatusBanner>
      ) : null}

      {banner ? (
        <InlineStatusBanner variant="success" surface="embedded">
          {banner}
        </InlineStatusBanner>
      ) : null}

      {listError ? (
        <InlineStatusBanner variant="error" surface="embedded">
          {listError}
        </InlineStatusBanner>
      ) : null}

      <SettingsSurface variant="embedded">
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
            <table className={embeddedTableClassName}>
              <thead className={embeddedDarkTableHeadClassName}>
                <tr>
                  <th scope="col" className={embeddedTableHeaderCellClassName}>Name</th>
                  {ownerScope === 'organization' ? <th scope="col" className={embeddedTableHeaderCellClassName}>Created By</th> : null}
                  <th scope="col" className={embeddedTableHeaderCellClassName}>Created</th>
                  <th scope="col" className={embeddedTableHeaderCellClassName}>Last Used</th>
                  <th scope="col" className={embeddedTableHeaderCellClassName}>Status</th>
                  {canManage ? <th scope="col" className={`${embeddedTableHeaderCellClassName} text-right`}>Actions</th> : null}
                </tr>
              </thead>
              <tbody className={embeddedDividedTableBodyClassName}>
                {keys.map((apiKey) => (
                  <tr key={apiKey.id} className={embeddedTableRowClassName}>
                    <td className={embeddedTableCellClassName}>
                      <div className="font-medium text-slate-100">{apiKey.name}</div>
                      <div className="mt-1 text-xs text-slate-400">Prefix: {apiKey.prefix}</div>
                    </td>
                    {ownerScope === 'organization' ? (
                      <td className={embeddedTableCellClassName}>{apiKey.created_by ?? '-'}</td>
                    ) : null}
                    <td className={embeddedTableCellClassName}>{formatDate(apiKey.created_at)}</td>
                    <td className={embeddedTableCellClassName}>{formatDate(apiKey.last_used_at)}</td>
                    <td className={embeddedTableCellClassName}>
                      <span className={apiKey.is_active
                        ? 'inline-flex rounded-full border border-green-300/30 bg-green-950/20 px-2 py-0.5 text-xs font-medium text-green-100'
                        : 'inline-flex rounded-full border border-red-300/30 bg-red-950/20 px-2 py-0.5 text-xs font-medium text-red-100'}
                      >
                        {apiKey.is_active ? 'Active' : 'Revoked'}
                      </span>
                    </td>
                    {canManage ? (
                      <td className={`${embeddedTableCellClassName} text-right`}>
                        <div className="flex items-center justify-end gap-1.5">
                          <EmbeddedTableActionButton
                            icon={Ban}
                            disabled={!apiKey.is_active}
                            onClick={() => openRevokeModal(apiKey)}
                            className={embeddedSecondaryActionButtonClassName}
                          >
                            Revoke
                          </EmbeddedTableActionButton>
                          <EmbeddedTableActionButton
                            icon={Trash2}
                            onClick={() => openDeleteModal(apiKey)}
                            className={embeddedCompactDestructiveButtonClassName}
                          >
                            Delete
                          </EmbeddedTableActionButton>
                        </div>
                      </td>
                    ) : null}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </SettingsSurface>
    </div>
  )
}
