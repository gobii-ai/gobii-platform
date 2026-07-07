import { Globe, KeyRound, Pencil, Trash2, ArrowUpFromLine, Terminal } from 'lucide-react'

import type { SecretDTO } from '../../api/secrets'
import { getSettingsSurfaceClassName } from '../common/SettingsSurface'

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
  const containerClassName = getSettingsSurfaceClassName({ variant: 'embedded', shadowClassName: 'shadow-none' })
  const headerClassName = 'border-b border-slate-200/15 px-6 py-4'
  const emptyIconClassName = 'flex h-12 w-12 items-center justify-center rounded-full border border-slate-300/70 bg-slate-900/40'
  const tableClassName = 'min-w-full divide-y divide-slate-200/15'
  const tableHeadClassName = 'bg-slate-900/40'
  const tableBodyClassName = 'divide-y divide-slate-200/15 bg-transparent'
  const rowClassName = 'hover:bg-slate-900/30'
  const codeClassName = 'rounded bg-slate-900/60 px-1.5 py-0.5 text-xs text-slate-200'
  const envBadgeClassName = 'inline-flex items-center gap-1 rounded-full border border-fuchsia-300/30 bg-fuchsia-950/30 px-2 py-0.5 text-xs font-medium text-fuchsia-100'
  const credentialBadgeClassName = 'inline-flex items-center gap-1 rounded-full border border-blue-300/30 bg-blue-950/30 px-2 py-0.5 text-xs font-medium text-blue-100'
  const secondaryActionClassName = 'inline-flex items-center gap-1 rounded border border-slate-300/70 bg-transparent px-2 py-1 text-xs font-medium text-slate-100 transition-colors hover:border-slate-200 hover:text-white'
  const promoteActionClassName = 'inline-flex items-center gap-1 rounded border border-blue-300/40 bg-blue-950/20 px-2 py-1 text-xs font-medium text-blue-100 transition-colors hover:border-blue-200 hover:bg-blue-900/30'
  const destructiveActionClassName = 'inline-flex items-center gap-1 rounded border border-rose-300/40 bg-rose-950/20 px-2 py-1 text-xs font-medium text-rose-100 transition-colors hover:border-rose-200 hover:bg-rose-900/30'

  return (
    <div className={containerClassName}>
      <div className={headerClassName}>
        <h2 className="text-lg font-semibold text-slate-100">{title}</h2>
        {subtitle && <p className="mt-1 text-sm text-slate-400">{subtitle}</p>}
      </div>

      {secrets.length === 0 ? (
        <div className="p-8 text-center">
          <div className="flex justify-center mb-4">
            <div className={emptyIconClassName}>
              <KeyRound className="h-6 w-6 text-slate-400" />
            </div>
          </div>
          <p className="text-sm text-slate-400">{emptyMessage}</p>
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className={tableClassName}>
            <thead className={tableHeadClassName}>
              <tr>
                <th scope="col" className="px-6 py-3 text-start text-xs font-semibold uppercase text-slate-300">
                  Name
                </th>
                <th scope="col" className="px-6 py-3 text-start text-xs font-semibold uppercase text-slate-300">
                  Key
                </th>
                <th scope="col" className="px-6 py-3 text-start text-xs font-semibold uppercase text-slate-300">
                  Type
                </th>
                <th scope="col" className="px-6 py-3 text-start text-xs font-semibold uppercase text-slate-300">
                  Scope
                </th>
                {!readOnly && (
                  <th scope="col" className="px-6 py-3 text-end text-xs font-semibold uppercase text-slate-300">
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
                        <Globe className="h-4 w-4 shrink-0 text-blue-300" />
                      ) : (
                        <KeyRound className="h-4 w-4 shrink-0 text-slate-400" />
                      )}
                      <div>
                        <p className="text-sm font-medium text-slate-100">{secret.name}</p>
                        {secret.description && (
                          <p className="max-w-xs truncate text-xs text-slate-400">{secret.description}</p>
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
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-slate-300">
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
