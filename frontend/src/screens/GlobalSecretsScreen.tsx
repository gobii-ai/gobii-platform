import { useCallback, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Globe, Key, Plus, Pencil, Trash2, ShieldCheck } from 'lucide-react'

import {
  fetchGlobalSecrets,
  createGlobalSecret,
  updateGlobalSecret,
  deleteGlobalSecret,
  type Secret,
  type SecretPayload,
  type SecretUpdatePayload,
} from '../api/secrets'
import { HttpError } from '../api/http'
import { Modal } from '../components/common/Modal'

type GlobalSecretsScreenProps = {
  secretsApiUrl: string
}

type SecretFormState = {
  secret_type: 'credential' | 'env_var'
  domain_pattern: string
  name: string
  description: string
  value: string
}

const emptyForm: SecretFormState = {
  secret_type: 'credential',
  domain_pattern: '',
  name: '',
  description: '',
  value: '',
}

function SecretForm({
  form,
  onChange,
  isEdit,
}: {
  form: SecretFormState
  onChange: (f: SecretFormState) => void
  isEdit: boolean
}) {
  const isEnvVar = form.secret_type === 'env_var'
  return (
    <div className="space-y-4">
      <div>
        <label className="block text-sm font-medium text-slate-700 mb-1">Secret Type</label>
        <select
          className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
          value={form.secret_type}
          onChange={(e) => onChange({ ...form, secret_type: e.target.value as 'credential' | 'env_var' })}
        >
          <option value="credential">Credential (domain scoped)</option>
          <option value="env_var">Environment Variable (sandbox env)</option>
        </select>
      </div>
      {!isEnvVar && (
        <div>
          <label className="block text-sm font-medium text-slate-700 mb-1">Domain Pattern</label>
          <input
            type="text"
            className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
            placeholder="e.g. https://api.example.com or *.google.com"
            value={form.domain_pattern}
            onChange={(e) => onChange({ ...form, domain_pattern: e.target.value })}
          />
        </div>
      )}
      <div>
        <label className="block text-sm font-medium text-slate-700 mb-1">Name</label>
        <input
          type="text"
          className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
          placeholder="e.g. API Key, X Password"
          value={form.name}
          onChange={(e) => onChange({ ...form, name: e.target.value })}
        />
      </div>
      <div>
        <label className="block text-sm font-medium text-slate-700 mb-1">Description (optional)</label>
        <input
          type="text"
          className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
          placeholder="What this secret is used for"
          value={form.description}
          onChange={(e) => onChange({ ...form, description: e.target.value })}
        />
      </div>
      <div>
        <label className="block text-sm font-medium text-slate-700 mb-1">
          {isEdit ? 'New Value (leave blank to keep current)' : 'Value'}
        </label>
        <input
          type="password"
          className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
          placeholder={isEdit ? 'Enter new value or leave blank' : 'Enter secret value'}
          value={form.value}
          onChange={(e) => onChange({ ...form, value: e.target.value })}
          autoComplete="off"
        />
      </div>
    </div>
  )
}

function SecretsTable({
  secrets,
  title,
  subtitle,
  type,
  onEdit,
  onDelete,
}: {
  secrets: Secret[]
  title: string
  subtitle: string
  type: 'credential' | 'env_var'
  onEdit: (s: Secret) => void
  onDelete: (s: Secret) => void
}) {
  const filtered = secrets.filter((s) => s.secret_type === type && !s.requested)
  if (filtered.length === 0) return null

  return (
    <div>
      <div className="px-6 pt-4 pb-2">
        <h3 className="text-sm font-semibold text-slate-700">{title}</h3>
        <p className="text-xs text-slate-500">{subtitle}</p>
      </div>
      <div className="overflow-x-auto">
        <table className="min-w-full divide-y divide-slate-200">
          <thead className="bg-slate-50">
            <tr>
              <th className="px-6 py-3 text-start text-xs font-semibold uppercase text-slate-800">Name</th>
              {type === 'credential' && (
                <th className="px-6 py-3 text-start text-xs font-semibold uppercase text-slate-800">Domain</th>
              )}
              {type === 'env_var' && (
                <th className="px-6 py-3 text-start text-xs font-semibold uppercase text-slate-800">Env Key</th>
              )}
              <th className="px-6 py-3 text-start text-xs font-semibold uppercase text-slate-800">Description</th>
              <th className="px-6 py-3 text-end text-xs font-semibold uppercase text-slate-800">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-200 bg-white">
            {filtered.map((s) => (
              <tr key={s.id} className="hover:bg-slate-50">
                <td className="px-6 py-4 whitespace-nowrap">
                  <p className="text-sm font-medium text-slate-900">{s.name}</p>
                  <p className="text-xs text-slate-500">
                    Key: <code className="bg-slate-100 px-1 rounded text-xs">{s.key}</code>
                  </p>
                </td>
                {type === 'credential' && (
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-slate-700">{s.domain_pattern}</td>
                )}
                {type === 'env_var' && (
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-slate-700">
                    <code className="bg-slate-100 px-1 rounded text-xs">{s.key}</code>
                  </td>
                )}
                <td className="px-6 py-4 text-sm text-slate-600">
                  {s.description || <span className="italic text-slate-400">No description</span>}
                </td>
                <td className="px-6 py-4 whitespace-nowrap text-end">
                  <div className="flex items-center justify-end gap-2">
                    <button
                      onClick={() => onEdit(s)}
                      className="inline-flex items-center gap-1 py-1 px-2 text-xs font-medium rounded border border-slate-200 bg-white text-slate-800 hover:bg-slate-50"
                    >
                      <Pencil className="w-3 h-3" /> Edit
                    </button>
                    <button
                      onClick={() => onDelete(s)}
                      className="inline-flex items-center gap-1 py-1 px-2 text-xs font-medium rounded border border-red-200 bg-red-50 text-red-700 hover:bg-red-100"
                    >
                      <Trash2 className="w-3 h-3" /> Delete
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export function GlobalSecretsScreen({ secretsApiUrl }: GlobalSecretsScreenProps) {
  const queryClient = useQueryClient()
  const [showAddModal, setShowAddModal] = useState(false)
  const [editingSecret, setEditingSecret] = useState<Secret | null>(null)
  const [deletingSecret, setDeletingSecret] = useState<Secret | null>(null)
  const [form, setForm] = useState<SecretFormState>(emptyForm)
  const [error, setError] = useState<string | null>(null)

  const { data, isLoading } = useQuery({
    queryKey: ['global-secrets'],
    queryFn: ({ signal }) => fetchGlobalSecrets(secretsApiUrl, signal),
  })

  const createMutation = useMutation({
    mutationFn: (payload: SecretPayload) => createGlobalSecret(secretsApiUrl, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['global-secrets'] })
      setShowAddModal(false)
      setForm(emptyForm)
      setError(null)
    },
    onError: (err: Error) => {
      setError(err instanceof HttpError ? String((err.body as Record<string, string>)?.error ?? err.message) : err.message)
    },
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: SecretUpdatePayload }) =>
      updateGlobalSecret(secretsApiUrl, id, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['global-secrets'] })
      setEditingSecret(null)
      setForm(emptyForm)
      setError(null)
    },
    onError: (err: Error) => {
      setError(err instanceof HttpError ? String((err.body as Record<string, string>)?.error ?? err.message) : err.message)
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteGlobalSecret(secretsApiUrl, id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['global-secrets'] })
      setDeletingSecret(null)
    },
  })

  const handleAdd = useCallback(() => {
    setForm(emptyForm)
    setError(null)
    setShowAddModal(true)
  }, [])

  const handleEdit = useCallback((s: Secret) => {
    setForm({
      secret_type: s.secret_type,
      domain_pattern: s.domain_pattern ?? '',
      name: s.name,
      description: s.description,
      value: '',
    })
    setError(null)
    setEditingSecret(s)
  }, [])

  const handleSubmitAdd = useCallback(() => {
    createMutation.mutate({
      secret_type: form.secret_type,
      domain_pattern: form.secret_type === 'env_var' ? undefined : form.domain_pattern,
      name: form.name,
      description: form.description,
      value: form.value,
    })
  }, [form, createMutation])

  const handleSubmitEdit = useCallback(() => {
    if (!editingSecret) return
    const payload: SecretUpdatePayload = {
      name: form.name,
      description: form.description,
      secret_type: form.secret_type,
    }
    if (form.secret_type !== 'env_var') {
      payload.domain_pattern = form.domain_pattern
    }
    if (form.value) {
      payload.value = form.value
    }
    updateMutation.mutate({ id: editingSecret.id, payload })
  }, [form, editingSecret, updateMutation])

  const secrets = data?.secrets ?? []

  return (
    <div className="max-w-6xl mx-auto px-4 py-6 space-y-6">
      {/* Header */}
      <div className="bg-white shadow-sm rounded-xl overflow-hidden">
        <div className="px-6 py-4 border-b border-slate-200 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold text-slate-800 flex items-center gap-2">
              <Globe className="w-6 h-6 text-blue-600" /> Global Secrets
            </h1>
            <p className="text-sm text-slate-500 mt-1">
              Manage secrets shared across all your agents
            </p>
          </div>
          <button
            onClick={handleAdd}
            className="inline-flex items-center gap-2 py-2 px-4 text-sm font-medium rounded-lg bg-blue-600 text-white hover:bg-blue-700 transition-colors"
          >
            <Plus className="w-4 h-4" /> Add Secret
          </button>
        </div>
      </div>

      {/* Security Notice */}
      <div className="bg-blue-50 border border-blue-200 rounded-xl p-4 sm:p-6">
        <div className="flex gap-x-4">
          <ShieldCheck className="w-6 h-6 text-blue-600 shrink-0" />
          <div>
            <h3 className="text-sm font-semibold text-blue-800 mb-1">Secure Encryption</h3>
            <p className="text-sm text-blue-700">
              All secrets are encrypted with AES-256-GCM before storage. Global secrets are available to all your agents.
            </p>
          </div>
        </div>
      </div>

      {/* Secrets Table */}
      <div className="bg-white shadow-sm rounded-xl overflow-hidden">
        <div className="px-6 py-4 border-b border-slate-200">
          <h2 className="text-lg font-semibold text-slate-800">Secrets</h2>
          <p className="text-sm text-slate-500 mt-1">
            {secrets.length > 0 ? `${secrets.length} global secret${secrets.length !== 1 ? 's' : ''} configured` : 'No global secrets configured yet'}
          </p>
        </div>

        {isLoading ? (
          <div className="p-8 text-center text-sm text-slate-500">Loading...</div>
        ) : secrets.length === 0 ? (
          <div className="p-8 text-center">
            <div className="flex justify-center mb-4">
              <div className="w-16 h-16 bg-slate-100 rounded-full flex items-center justify-center">
                <Key className="w-8 h-8 text-slate-400" />
              </div>
            </div>
            <h3 className="text-lg font-medium text-slate-900 mb-2">No global secrets configured</h3>
            <p className="text-sm text-slate-500 mb-4">
              Add your first global secret to share it across all your agents.
            </p>
          </div>
        ) : (
          <>
            <SecretsTable
              secrets={secrets}
              title="Credential Secrets (Domain Scoped)"
              subtitle="Available to all agents for matching domains"
              type="credential"
              onEdit={handleEdit}
              onDelete={setDeletingSecret}
            />
            <SecretsTable
              secrets={secrets}
              title="Environment Variable Secrets"
              subtitle="Injected into sandbox execution for all agents"
              type="env_var"
              onEdit={handleEdit}
              onDelete={setDeletingSecret}
            />
          </>
        )}
      </div>

      {/* Add Modal */}
      {showAddModal && (
        <Modal
          title="Add Global Secret"
          subtitle="This secret will be available to all your agents"
          icon={Key}
          iconBgClass="bg-blue-100"
          iconColorClass="text-blue-600"
          onClose={() => { setShowAddModal(false); setError(null) }}
          footer={
            <>
              <button
                onClick={handleSubmitAdd}
                disabled={createMutation.isPending}
                className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-50"
              >
                {createMutation.isPending ? 'Adding...' : 'Add Secret'}
              </button>
              <button
                onClick={() => { setShowAddModal(false); setError(null) }}
                className="rounded-lg border border-slate-200 bg-white px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50"
              >
                Cancel
              </button>
            </>
          }
        >
          {error && <p className="text-sm text-red-600 mb-3">{error}</p>}
          <SecretForm form={form} onChange={setForm} isEdit={false} />
        </Modal>
      )}

      {/* Edit Modal */}
      {editingSecret && (
        <Modal
          title="Edit Global Secret"
          subtitle={`Editing "${editingSecret.name}"`}
          icon={Pencil}
          iconBgClass="bg-amber-100"
          iconColorClass="text-amber-600"
          onClose={() => { setEditingSecret(null); setError(null) }}
          footer={
            <>
              <button
                onClick={handleSubmitEdit}
                disabled={updateMutation.isPending}
                className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-50"
              >
                {updateMutation.isPending ? 'Saving...' : 'Save Changes'}
              </button>
              <button
                onClick={() => { setEditingSecret(null); setError(null) }}
                className="rounded-lg border border-slate-200 bg-white px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50"
              >
                Cancel
              </button>
            </>
          }
        >
          {error && <p className="text-sm text-red-600 mb-3">{error}</p>}
          <SecretForm form={form} onChange={setForm} isEdit={true} />
        </Modal>
      )}

      {/* Delete Confirmation Modal */}
      {deletingSecret && (
        <Modal
          title="Delete Secret"
          subtitle={`Are you sure you want to delete "${deletingSecret.name}"?`}
          icon={Trash2}
          iconBgClass="bg-red-100"
          iconColorClass="text-red-600"
          onClose={() => setDeletingSecret(null)}
          footer={
            <>
              <button
                onClick={() => deleteMutation.mutate(deletingSecret.id)}
                disabled={deleteMutation.isPending}
                className="inline-flex items-center gap-2 rounded-lg bg-red-600 px-4 py-2 text-sm font-semibold text-white hover:bg-red-700 disabled:opacity-50"
              >
                {deleteMutation.isPending ? 'Deleting...' : 'Delete'}
              </button>
              <button
                onClick={() => setDeletingSecret(null)}
                className="rounded-lg border border-slate-200 bg-white px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50"
              >
                Cancel
              </button>
            </>
          }
        >
          <p className="text-sm text-slate-600">
            This action cannot be undone. The secret will be permanently removed and all agents will lose access to it.
          </p>
        </Modal>
      )}
    </div>
  )
}
