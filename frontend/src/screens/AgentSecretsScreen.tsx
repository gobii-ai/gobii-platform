import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Trash2, Plus, Globe, Lock } from 'lucide-react'

import {
  fetchAgentSecrets,
  fetchGlobalSecrets,
  createSecret,
  deleteSecret,
  type CreateSecretPayload,
  type SecretType,
} from '../api/secrets'

type AgentSecretsScreenProps = {
  agentId: string
  agentName: string | null
}

export function AgentSecretsScreen({ agentId, agentName }: AgentSecretsScreenProps) {
  // Use agentName if needed or ignore it with a comment
  // @ts-ignore
  const _ignored = agentName;
  const queryClient = useQueryClient()

  const { data: agentSecrets = [], isLoading: isLoadingAgent } = useQuery({
    queryKey: ['agentSecrets', agentId],
    queryFn: () => fetchAgentSecrets(agentId),
  })

  const { data: globalSecrets = [], isLoading: isLoadingGlobal } = useQuery({
    queryKey: ['globalSecrets'],
    queryFn: () => fetchGlobalSecrets(),
  })

  const [isAdding, setIsAdding] = useState(false)
  const [newName, setNewName] = useState('')
  const [newType, setNewType] = useState<SecretType>('credential')
  const [newDomain, setNewDomain] = useState('')
  const [newValue, setNewValue] = useState('')
  const [newIsGlobal, setNewIsGlobal] = useState(false)

  const createMutation = useMutation({
    mutationFn: (payload: CreateSecretPayload) => createSecret(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['agentSecrets', agentId] })
      queryClient.invalidateQueries({ queryKey: ['globalSecrets'] })
      setIsAdding(false)
      setNewName('')
      setNewDomain('')
      setNewValue('')
      setNewIsGlobal(false)
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteSecret(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['agentSecrets', agentId] })
      queryClient.invalidateQueries({ queryKey: ['globalSecrets'] })
    },
  })

  const handleCreate = (e: React.FormEvent) => {
    e.preventDefault()
    createMutation.mutate({
      name: newName,
      secret_type: newType,
      domain_pattern: newType === 'credential' ? newDomain : undefined,
      value: newValue,
      is_global: newIsGlobal,
      agent: newIsGlobal ? null : agentId,
    })
  }

  if (isLoadingAgent || isLoadingGlobal) {
    return <div className="text-center py-12 text-slate-500">Loading secrets...</div>
  }

  return (
    <div className="space-y-8">
      <div>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-2xl font-bold text-slate-900">Agent Secrets</h2>
          <button
            onClick={() => setIsAdding(true)}
            className="flex items-center gap-2 px-4 py-2 bg-slate-900 text-white rounded-md hover:bg-slate-800 transition-colors text-sm font-medium"
          >
            <Plus size={16} />
            Add Secret
          </button>
        </div>

        {agentSecrets.length === 0 ? (
          <div className="bg-slate-50 border border-slate-200 rounded-lg p-8 text-center">
            <Lock className="mx-auto h-8 w-8 text-slate-400 mb-3" />
            <h3 className="text-sm font-medium text-slate-900">No agent secrets</h3>
            <p className="text-sm text-slate-500 mt-1">
              Add a secret to securely provide credentials or environment variables to this agent.
            </p>
          </div>
        ) : (
          <div className="border border-slate-200 rounded-lg overflow-hidden">
            <table className="w-full text-sm text-left">
              <thead className="bg-slate-50 border-b border-slate-200 text-slate-600">
                <tr>
                  <th className="px-6 py-3 font-medium">Name</th>
                  <th className="px-6 py-3 font-medium">Type</th>
                  <th className="px-6 py-3 font-medium">Domain Pattern</th>
                  <th className="px-6 py-3 font-medium text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-200 bg-white">
                {agentSecrets.map((secret) => (
                  <tr key={secret.id}>
                    <td className="px-6 py-4 font-medium text-slate-900">{secret.name}</td>
                    <td className="px-6 py-4 text-slate-600">
                      <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-slate-100 text-slate-800">
                        {secret.secret_type === 'credential' ? 'Credential' : 'Env Var'}
                      </span>
                    </td>
                    <td className="px-6 py-4 text-slate-600">
                      {secret.secret_type === 'credential' ? <code className="text-xs bg-slate-50 px-1 py-0.5 rounded">{secret.domain_pattern}</code> : '-'}
                    </td>
                    <td className="px-6 py-4 text-right">
                      <button
                        onClick={() => deleteMutation.mutate(secret.id)}
                        className="text-red-500 hover:text-red-700 p-1 rounded hover:bg-red-50 transition-colors"
                        title="Delete secret"
                        disabled={deleteMutation.isPending}
                      >
                        <Trash2 size={16} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div>
        <div className="flex items-center gap-2 mb-4">
          <Globe className="text-slate-400" size={20} />
          <h2 className="text-2xl font-bold text-slate-900">Global Secrets</h2>
        </div>
        <p className="text-sm text-slate-500 mb-4">
          Global secrets are available to all your agents. Manage them here or on the global secrets page.
        </p>

        {globalSecrets.length === 0 ? (
          <div className="bg-slate-50 border border-slate-200 rounded-lg p-8 text-center">
            <Globe className="mx-auto h-8 w-8 text-slate-400 mb-3" />
            <h3 className="text-sm font-medium text-slate-900">No global secrets</h3>
            <p className="text-sm text-slate-500 mt-1">
              Global secrets can be shared across all your agents.
            </p>
          </div>
        ) : (
          <div className="border border-slate-200 rounded-lg overflow-hidden">
            <table className="w-full text-sm text-left">
              <thead className="bg-slate-50 border-b border-slate-200 text-slate-600">
                <tr>
                  <th className="px-6 py-3 font-medium">Name</th>
                  <th className="px-6 py-3 font-medium">Type</th>
                  <th className="px-6 py-3 font-medium">Domain Pattern</th>
                  <th className="px-6 py-3 font-medium text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-200 bg-white">
                {globalSecrets.map((secret) => (
                  <tr key={secret.id}>
                    <td className="px-6 py-4 font-medium text-slate-900">{secret.name}</td>
                    <td className="px-6 py-4 text-slate-600">
                      <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-slate-100 text-slate-800">
                        {secret.secret_type === 'credential' ? 'Credential' : 'Env Var'}
                      </span>
                    </td>
                    <td className="px-6 py-4 text-slate-600">
                      {secret.secret_type === 'credential' ? <code className="text-xs bg-slate-50 px-1 py-0.5 rounded">{secret.domain_pattern}</code> : '-'}
                    </td>
                    <td className="px-6 py-4 text-right">
                      <button
                        onClick={() => deleteMutation.mutate(secret.id)}
                        className="text-red-500 hover:text-red-700 p-1 rounded hover:bg-red-50 transition-colors"
                        title="Delete secret"
                        disabled={deleteMutation.isPending}
                      >
                        <Trash2 size={16} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {isAdding && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 p-4">
          <div className="bg-white rounded-lg shadow-xl w-full max-w-md overflow-hidden">
            <div className="px-6 py-4 border-b border-slate-100">
              <h3 className="text-lg font-bold text-slate-900">Add New Secret</h3>
            </div>
            <form onSubmit={handleCreate} className="p-6 space-y-4">
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">Secret Type</label>
                <select
                  value={newType}
                  onChange={(e) => setNewType(e.target.value as SecretType)}
                  className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                >
                  <option value="credential">HTTP Credential</option>
                  <option value="env_var">Environment Variable</option>
                </select>
              </div>

              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">Name</label>
                <input
                  type="text"
                  required
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  placeholder={newType === 'env_var' ? 'e.g. API_KEY' : 'e.g. Github Token'}
                  className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                />
              </div>

              {newType === 'credential' && (
                <div>
                  <label className="block text-sm font-medium text-slate-700 mb-1">Domain Pattern</label>
                  <input
                    type="text"
                    required
                    value={newDomain}
                    onChange={(e) => setNewDomain(e.target.value)}
                    placeholder="e.g. api.github.com"
                    className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                  />
                  <p className="text-xs text-slate-500 mt-1">Credentials will only be sent to matching domains.</p>
                </div>
              )}

              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">Value</label>
                <textarea
                  required
                  value={newValue}
                  onChange={(e) => setNewValue(e.target.value)}
                  placeholder="Enter the secret value..."
                  rows={3}
                  className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 font-mono"
                />
              </div>

              <div className="flex items-center gap-2 pt-2">
                <input
                  type="checkbox"
                  id="isGlobalToggle"
                  checked={newIsGlobal}
                  onChange={(e) => setNewIsGlobal(e.target.checked)}
                  className="rounded border-slate-300 text-blue-600 focus:ring-blue-500"
                />
                <label htmlFor="isGlobalToggle" className="text-sm text-slate-700 font-medium">
                  Make this a global secret
                </label>
              </div>
              <p className="text-xs text-slate-500 pl-6">
                Global secrets can be used by all agents in your account.
              </p>

              <div className="flex justify-end gap-3 pt-4 border-t border-slate-100 mt-6">
                <button
                  type="button"
                  onClick={() => setIsAdding(false)}
                  className="px-4 py-2 text-sm font-medium text-slate-700 bg-white border border-slate-300 rounded-md hover:bg-slate-50 focus:outline-none"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={createMutation.isPending}
                  className="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-md hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:opacity-50"
                >
                  {createMutation.isPending ? 'Saving...' : 'Save Secret'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  )
}
