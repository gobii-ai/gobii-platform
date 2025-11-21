import { CircleDot, Loader2, CheckCircle2, XCircle } from 'lucide-react'

export type Status = 'pending' | 'running' | 'completed' | 'errored' | 'passed' | 'failed'

const statusStyles: Record<Status, { bg: string; text: string; icon: React.ElementType; label: string }> = {
  pending: { bg: 'bg-slate-100 text-slate-700', text: 'Pending', icon: CircleDot, label: 'Pending' },
  running: { bg: 'bg-blue-100 text-blue-700', text: 'Running', icon: Loader2, label: 'Running' },
  completed: { bg: 'bg-emerald-100 text-emerald-700', text: 'Completed', icon: CheckCircle2, label: 'Completed' },
  passed: { bg: 'bg-emerald-100 text-emerald-700', text: 'Passed', icon: CheckCircle2, label: 'Passed' },
  errored: { bg: 'bg-rose-100 text-rose-700', text: 'Errored', icon: XCircle, label: 'Errored' },
  failed: { bg: 'bg-rose-100 text-rose-700', text: 'Failed', icon: XCircle, label: 'Failed' },
}

interface StatusBadgeProps {
  status: string
  className?: string
  animate?: boolean
  label?: string // Override label
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
