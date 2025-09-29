import type { ToolCallEntry } from './types'

type ToolStepDetailsProps = {
  entry: ToolCallEntry
  inline?: boolean
}

export function ToolStepDetails({ entry, inline = false }: ToolStepDetailsProps) {
  const wrapperClass = inline
    ? entry.result || entry.parameters
      ? 'space-y-3 text-xs text-slate-600'
      : 'text-xs text-slate-600'
    : 'space-y-3 rounded-2xl border border-white/40 bg-white/90 p-3 text-[11px] text-slate-600 shadow-inner'

  const showSql = Array.isArray(entry.sqlStatements) && entry.sqlStatements.length > 0
  const showParams = Boolean(entry.parameters) && !showSql
  const showResult = typeof entry.result === 'string' && entry.result.length > 0

  return (
    <div className={wrapperClass}>
      <div className="flex flex-wrap gap-4">
        {entry.toolName ? (
          <span>
            <span className="font-semibold text-slate-700">Tool:</span> {entry.toolName}
          </span>
        ) : null}
        {entry.caption ? (
          <span>
            <span className="font-semibold text-slate-700">Summary:</span> {entry.caption}
          </span>
        ) : null}
      </div>

      {showSql ? (
        <div className="space-y-2">
          <p className="font-semibold text-slate-700">SQL statements</p>
          {entry.sqlStatements?.map((statement, idx) => (
            <div key={idx} className="overflow-auto rounded-xl bg-slate-900/95 p-3 shadow-inner">
              <pre className="text-[11px] text-emerald-100">
                <code className="language-sql">{statement}</code>
              </pre>
            </div>
          ))}
        </div>
      ) : null}

      {showParams ? (
        <div>
          <p className="font-semibold text-slate-700">Parameters</p>
          <pre className="max-h-48 overflow-auto rounded-xl bg-slate-900/95 p-3 text-[11px] text-slate-100 shadow-inner">
            {JSON.stringify(entry.parameters, null, 2)}
          </pre>
        </div>
      ) : null}

      {showResult ? (
        <div>
          <p className="font-semibold text-slate-700">Result</p>
          <div className="max-h-48 overflow-auto rounded-xl bg-slate-50 p-3 text-[11px] text-slate-700 shadow-inner whitespace-pre-wrap">
            {entry.result}
          </div>
        </div>
      ) : null}
    </div>
  )
}
