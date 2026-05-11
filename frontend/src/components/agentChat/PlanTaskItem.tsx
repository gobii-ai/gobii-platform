import { memo } from 'react'
import { CheckCircle2, Circle, CirclePause, LoaderCircle } from 'lucide-react'

export type PlanTaskStatus = 'done' | 'doing' | 'todo'

type PlanTaskItemProps = {
  title: string
  status: PlanTaskStatus
  isAgentWorking: boolean
}

export const PlanTaskItem = memo(function PlanTaskItem({ title, status, isAgentWorking }: PlanTaskItemProps) {
  const isDoing = status === 'doing'
  const isPausedDoing = isDoing && !isAgentWorking
  const workState = isDoing ? (isPausedDoing ? 'paused' : 'active') : undefined

  return (
    <li className="plan-panel-task" data-status={status} data-work-state={workState}>
      <span className="plan-panel-task-icon" aria-hidden="true">
        {status === 'done' ? (
          <CheckCircle2 size={14} strokeWidth={2.4} />
        ) : isPausedDoing ? (
          <CirclePause size={14} strokeWidth={2.4} />
        ) : isDoing ? (
          <LoaderCircle size={14} strokeWidth={2.4} />
        ) : (
          <Circle size={14} strokeWidth={2.2} />
        )}
      </span>
      <span className="plan-panel-task-title">{title}</span>
    </li>
  )
})
