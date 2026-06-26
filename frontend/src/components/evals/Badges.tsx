import { CheckCircle2, CircleDot, Loader2, XCircle } from 'lucide-react'
import type { ElementType } from 'react'
import type { EvalRunType } from '../../api/evals'

export type Status = 'pending' | 'running' | 'completed' | 'errored' | 'passed' | 'failed'

const statusStyles: Record<Status, { bg: string; icon: ElementType; label: string }> = {
  pending: { bg: 'bg-slate-100 text-slate-700', icon: CircleDot, label: 'Pending' },
  running: { bg: 'bg-blue-100 text-blue-700', icon: Loader2, label: 'Running' },
  completed: { bg: 'bg-emerald-100 text-emerald-700', icon: CheckCircle2, label: 'Completed' },
  passed: { bg: 'bg-emerald-100 text-emerald-700', icon: CheckCircle2, label: 'Passed' },
  errored: { bg: 'bg-rose-100 text-rose-700', icon: XCircle, label: 'Errored' },
  failed: { bg: 'bg-rose-100 text-rose-700', icon: XCircle, label: 'Failed' },
}

type StatusBadgeProps = {
  status: string
  className?: string
  animate?: boolean
  label?: string
}

export function StatusBadge({ status, className = '', animate = true, label }: StatusBadgeProps) {
  const normalizedStatus = (statusStyles[status as Status] ? status : 'pending') as Status
  const preset = statusStyles[normalizedStatus]
  const Icon = preset.icon

  return (
    <span className={`inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium border border-transparent ${preset.bg} ${className}`}>
      <Icon className={`w-3.5 h-3.5 ${animate && normalizedStatus === 'running' ? 'animate-spin' : ''}`} />
      {label || preset.label}
    </span>
  )
}

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
