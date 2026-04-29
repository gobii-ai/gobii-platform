import { Globe, KeyRound, Pencil, Trash2, ArrowUpFromLine, Terminal } from 'lucide-react'

import type { SecretDTO } from '../../api/secrets'

type SecretTableProps = {
  secrets: SecretDTO[]
  /** When true, hide action buttons (used for read-only global secrets on agent page). */
  readOnly?: boolean
  embedded?: boolean
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
  embedded = false,
  title,
  subtitle,
  emptyMessage = 'No secrets configured.',
  onEdit,
  onDelete,
  onPromote,
}: SecretTableProps) {
  const containerClassName = embedded
    ? 'overflow-hidden rounded-xl border border-slate-200/70 bg-transparent shadow-none'
    : 'gobii-card-base'
  const headerClassName = embedded
    ? 'border-b border-slate-200/70 px-6 py-4'
    : 'px-6 py-4 border-b border-gray-200/70'
  const emptyIconClassName = embedded
    ? 'flex h-12 w-12 items-center justify-center rounded-full border border-slate-300/70 bg-slate-900/40'
    : 'flex h-12 w-12 items-center justify-center rounded-full bg-gray-100'
  const tableClassName = embedded ? 'min-w-full divide-y divide-slate-200/70' : 'min-w-full divide-y divide-gray-200'
  const tableHeadClassName = embedded ? 'bg-slate-900/40' : 'bg-gray-50'
  const tableBodyClassName = embedded ? 'divide-y divide-slate-200/70 bg-transparent' : 'divide-y divide-gray-200 bg-white'
  const rowClassName = embedded ? 'hover:bg-slate-900/30' : 'hover:bg-gray-50'
  const codeClassName = embedded
    ? 'rounded bg-slate-900/60 px-1.5 py-0.5 text-xs text-slate-200'
    : 'bg-gray-100 px-1.5 py-0.5 rounded text-xs text-gray-700'
  const envBadgeClassName = embedded
    ? 'inline-flex items-center gap-1 rounded-full border border-fuchsia-300/30 bg-fuchsia-950/30 px-2 py-0.5 text-xs font-medium text-fuchsia-100'
    : 'inline-flex items-center gap-1 rounded-full bg-purple-50 px-2 py-0.5 text-xs font-medium text-purple-700'
  const credentialBadgeClassName = embedded
    ? 'inline-flex items-center gap-1 rounded-full border border-blue-300/30 bg-blue-950/30 px-2 py-0.5 text-xs font-medium text-blue-100'
    : 'inline-flex items-center gap-1 rounded-full bg-blue-50 px-2 py-0.5 text-xs font-medium text-blue-700'
  const secondaryActionClassName = embedded
    ? 'inline-flex items-center gap-1 rounded border border-slate-300/70 bg-transparent px-2 py-1 text-xs font-medium text-slate-100 transition-colors hover:border-slate-200 hover:text-white'
    : 'inline-flex items-center gap-1 py-1 px-2 text-xs font-medium rounded border border-gray-200 bg-white text-gray-700 hover:bg-gray-50 transition-colors'
  const promoteActionClassName = embedded
    ? 'inline-flex items-center gap-1 rounded border border-blue-300/40 bg-blue-950/20 px-2 py-1 text-xs font-medium text-blue-100 transition-colors hover:border-blue-200 hover:bg-blue-900/30'
    : 'inline-flex items-center gap-1 py-1 px-2 text-xs font-medium rounded border border-blue-200 bg-blue-50 text-blue-700 hover:bg-blue-100 transition-colors'
  const destructiveActionClassName = embedded
    ? 'inline-flex items-center gap-1 rounded border border-rose-300/40 bg-rose-950/20 px-2 py-1 text-xs font-medium text-rose-100 transition-colors hover:border-rose-200 hover:bg-rose-900/30'
    : 'inline-flex items-center gap-1 py-1 px-2 text-xs font-medium rounded border border-red-200 bg-red-50 text-red-700 hover:bg-red-100 transition-colors'

  return (
    <div className={containerClassName}>
      <div className={headerClassName}>
        <h2 className={embedded ? 'text-lg font-semibold text-slate-100' : 'text-lg font-semibold text-gray-800'}>{title}</h2>
        {subtitle && <p className={embedded ? 'mt-1 text-sm text-slate-400' : 'text-sm text-gray-500 mt-1'}>{subtitle}</p>}
      </div>

      {secrets.length === 0 ? (
        <div className="p-8 text-center">
          <div className="flex justify-center mb-4">
            <div className={emptyIconClassName}>
              <KeyRound className={embedded ? 'h-6 w-6 text-slate-400' : 'w-6 h-6 text-gray-400'} />
            </div>
          </div>
          <p className={embedded ? 'text-sm text-slate-400' : 'text-sm text-gray-500'}>{emptyMessage}</p>
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className={tableClassName}>
            <thead className={tableHeadClassName}>
              <tr>
                <th scope="col" className={embedded ? 'px-6 py-3 text-start text-xs font-semibold uppercase text-slate-300' : 'px-6 py-3 text-start text-xs font-semibold uppercase text-gray-800'}>
                  Name
                </th>
                <th scope="col" className={embedded ? 'px-6 py-3 text-start text-xs font-semibold uppercase text-slate-300' : 'px-6 py-3 text-start text-xs font-semibold uppercase text-gray-800'}>
                  Key
                </th>
                <th scope="col" className={embedded ? 'px-6 py-3 text-start text-xs font-semibold uppercase text-slate-300' : 'px-6 py-3 text-start text-xs font-semibold uppercase text-gray-800'}>
                  Type
                </th>
                <th scope="col" className={embedded ? 'px-6 py-3 text-start text-xs font-semibold uppercase text-slate-300' : 'px-6 py-3 text-start text-xs font-semibold uppercase text-gray-800'}>
                  Scope
                </th>
                {!readOnly && (
                  <th scope="col" className={embedded ? 'px-6 py-3 text-end text-xs font-semibold uppercase text-slate-300' : 'px-6 py-3 text-end text-xs font-semibold uppercase text-gray-800'}>
                    Actions
                  </th>
                )}
              </tr>
            </thead>
            <tbody className={tableBodyClassName}>
              {secrets.map((secret) => (
                <tr key={secret.id} className={rowClassName}>
                  <td className="px-6 py-4 whitespace-nowrap">
                    <div className="flex items-center gap-2">
                      {secret.source === 'global' ? (
                        <Globe className={embedded ? 'h-4 w-4 shrink-0 text-blue-300' : 'w-4 h-4 text-blue-500 flex-shrink-0'} />
                      ) : (
                        <KeyRound className={embedded ? 'h-4 w-4 shrink-0 text-slate-400' : 'w-4 h-4 text-gray-400 flex-shrink-0'} />
                      )}
                      <div>
                        <p className={embedded ? 'text-sm font-medium text-slate-100' : 'text-sm font-medium text-gray-900'}>{secret.name}</p>
                        {secret.description && (
                          <p className={embedded ? 'max-w-xs truncate text-xs text-slate-400' : 'text-xs text-gray-500 truncate max-w-xs'}>{secret.description}</p>
                        )}
                      </div>
                    </div>
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap">
                    <code className={codeClassName}>{secret.key}</code>
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap">
                    {secret.secret_type === 'env_var' ? (
                      <span className={envBadgeClassName}>
                        <Terminal className="w-3 h-3" />
                        Env Var
                      </span>
                    ) : (
                      <span className={credentialBadgeClassName}>
                        <KeyRound className="w-3 h-3" />
                        Credential
                      </span>
                    )}
                  </td>
                  <td className={embedded ? 'px-6 py-4 whitespace-nowrap text-sm text-slate-300' : 'px-6 py-4 whitespace-nowrap text-sm text-gray-600'}>
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
                            className={secondaryActionClassName}
                          >
                            <Pencil className="w-3 h-3" />
                            Edit
                          </button>
                        )}
                        {onPromote && secret.source === 'agent' && (
                          <button
                            type="button"
                            onClick={() => onPromote(secret)}
                            className={promoteActionClassName}
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
                            className={destructiveActionClassName}
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
