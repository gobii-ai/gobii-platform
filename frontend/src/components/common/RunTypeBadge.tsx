import type { EvalRunType } from '../../api/evals'

type RunTypeBadgeProps = {
  runType: EvalRunType
  dense?: boolean
}

export function RunTypeBadge({ runType, dense = false }: RunTypeBadgeProps) {
  const isOfficial = runType === 'official'
  const sizeClasses = dense ? 'px-2 py-0.5 text-[10px]' : 'px-2.5 py-1 text-xs'
  const tone = isOfficial
    ? 'bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200'
    : 'bg-slate-50 text-slate-600 ring-1 ring-slate-200'

  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full font-semibold ${sizeClasses} ${tone}`}>
      <span className={`h-2 w-2 rounded-full ${isOfficial ? 'bg-emerald-500' : 'bg-slate-400'}`} />
      {isOfficial ? 'Official' : 'One-off'}
    </span>
  )
}
