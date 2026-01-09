import type { ToolDetailProps } from '../../tooling/types'
import { isNonEmptyString } from '../utils'

export function AnalysisToolDetail({ entry }: ToolDetailProps) {
  const content = isNonEmptyString(entry.result) ? entry.result : entry.summary || entry.caption || null
  return (
    <div className="space-y-3 text-sm text-slate-600">
      {content ? (
        <div className="whitespace-pre-wrap text-sm text-slate-700">{content}</div>
      ) : (
        <p>No analysis output was captured.</p>
      )}
    </div>
  )
}
