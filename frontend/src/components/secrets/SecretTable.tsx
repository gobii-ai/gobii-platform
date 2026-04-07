import { Globe, KeyRound, Pencil, Trash2, ArrowUpFromLine, Terminal } from 'lucide-react'

import type { SecretDTO } from '../../api/secrets'

type SecretTableProps = {
  secrets: SecretDTO[]
  /** When true, hide action buttons (used for read-only global secrets on agent page). */
  readOnly?: boolean
  /** Label shown above the table. */
  title: string
  subtitle?: string
  emptyMessage?: string
  onEdit?: (secret: SecretDTO) => void
  onDelete?: (secret: SecretDTO) => void
  /** Promote agent secret to global. */
  onPromote?: (secret: SecretDTO) => void
}

export function SecretTable({
  secrets,
  readOnly = false,
  title,
  subtitle,
  emptyMessage = 'No secrets configured.',
  onEdit,
  onDelete,
  onPromote,
}: SecretTableProps) {
  return (
    <div className="gobii-card-base">
      <div className="px-6 py-4 border-b border-gray-200/70">
        <h2 className="text-lg font-semibold text-gray-800">{title}</h2>
        {subtitle && <p className="text-sm text-gray-500 mt-1">{subtitle}</p>}
      </div>

      {secrets.length === 0 ? (
        <div className="p-8 text-center">
          <div className="flex justify-center mb-4">
            <div className="w-12 h-12 bg-gray-100 rounded-full flex items-center justify-center">
              <KeyRound className="w-6 h-6 text-gray-400" />
            </div>
          </div>
          <p className="text-sm text-gray-500">{emptyMessage}</p>
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th scope="col" className="px-6 py-3 text-start text-xs font-semibold uppercase text-gray-800">
                  Name
                </th>
                <th scope="col" className="px-6 py-3 text-start text-xs font-semibold uppercase text-gray-800">
                  Key
                </th>
                <th scope="col" className="px-6 py-3 text-start text-xs font-semibold uppercase text-gray-800">
                  Type
                </th>
                <th scope="col" className="px-6 py-3 text-start text-xs font-semibold uppercase text-gray-800">
                  Scope
                </th>
                {!readOnly && (
                  <th scope="col" className="px-6 py-3 text-end text-xs font-semibold uppercase text-gray-800">
                    Actions
                  </th>
                )}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200 bg-white">
              {secrets.map((secret) => (
                <tr key={secret.id} className="hover:bg-gray-50">
                  <td className="px-6 py-4 whitespace-nowrap">
                    <div className="flex items-center gap-2">
                      {secret.source === 'global' ? (
                        <Globe className="w-4 h-4 text-blue-500 flex-shrink-0" />
                      ) : (
                        <KeyRound className="w-4 h-4 text-gray-400 flex-shrink-0" />
                      )}
                      <div>
                        <p className="text-sm font-medium text-gray-900">{secret.name}</p>
                        {secret.description && (
                          <p className="text-xs text-gray-500 truncate max-w-xs">{secret.description}</p>
                        )}
                      </div>
                    </div>
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap">
                    <code className="bg-gray-100 px-1.5 py-0.5 rounded text-xs text-gray-700">{secret.key}</code>
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap">
                    {secret.secret_type === 'env_var' ? (
                      <span className="inline-flex items-center gap-1 rounded-full bg-purple-50 px-2 py-0.5 text-xs font-medium text-purple-700">
                        <Terminal className="w-3 h-3" />
                        Env Var
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 rounded-full bg-blue-50 px-2 py-0.5 text-xs font-medium text-blue-700">
                        <KeyRound className="w-3 h-3" />
                        Credential
                      </span>
                    )}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-600">
                    {secret.secret_type === 'env_var'
                      ? 'Sandbox'
                      : secret.domain_pattern}
                  </td>
                  {!readOnly && (
                    <td className="px-6 py-4 whitespace-nowrap text-end">
                      <div className="flex items-center justify-end gap-1.5">
                        {onEdit && (
                          <button
                            type="button"
                            onClick={() => onEdit(secret)}
                            className="inline-flex items-center gap-1 py-1 px-2 text-xs font-medium rounded border border-gray-200 bg-white text-gray-700 hover:bg-gray-50 transition-colors"
                          >
                            <Pencil className="w-3 h-3" />
                            Edit
                          </button>
                        )}
                        {onPromote && secret.source === 'agent' && (
                          <button
                            type="button"
                            onClick={() => onPromote(secret)}
                            className="inline-flex items-center gap-1 py-1 px-2 text-xs font-medium rounded border border-blue-200 bg-blue-50 text-blue-700 hover:bg-blue-100 transition-colors"
                            title="Promote to global secret"
                          >
                            <ArrowUpFromLine className="w-3 h-3" />
                            Make Global
                          </button>
                        )}
                        {onDelete && (
                          <button
                            type="button"
                            onClick={() => onDelete(secret)}
                            className="inline-flex items-center gap-1 py-1 px-2 text-xs font-medium rounded border border-red-200 bg-red-50 text-red-700 hover:bg-red-100 transition-colors"
                          >
                            <Trash2 className="w-3 h-3" />
                            Delete
                          </button>
                        )}
                      </div>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
