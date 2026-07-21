import { AlertTriangle, CheckCircle2, CornerDownRight } from 'lucide-react'

export type EvalFailureTarget = {
  runId: string
  runIndex: number
  targetId: string
}

export type EvalFailureGroup = {
  runId: string
  runIndex: number
  scenarioSlug: string
  runTargetId: string
  isRunError: boolean
  tasks: Array<{
    id: number
    name: string
    sequence: number
    status: string
    targetId: string
  }>
}

type FailureSummaryProps = {
  groups: EvalFailureGroup[]
  showSuccess?: boolean
  onNavigate: (target: EvalFailureTarget) => void
}

export function FailureSummary({ groups, showSuccess = false, onNavigate }: FailureSummaryProps) {
  if (groups.length === 0) {
    if (!showSuccess) return null
    return (
      <section className="flex items-center gap-3 rounded-xl border border-emerald-200 bg-emerald-50 px-5 py-3 text-emerald-900" role="status">
        <CheckCircle2 className="h-5 w-5 shrink-0 text-emerald-600" />
        <div>
          <h2 className="text-sm font-bold uppercase tracking-wide">No failures recorded</h2>
          <p className="text-xs text-emerald-700">This eval run completed without failed assertions or execution errors.</p>
        </div>
      </section>
    )
  }

  const failureCount = groups.reduce(
    (count, group) => count + (group.tasks.length || (group.isRunError ? 1 : 0)),
    0,
  )

  return (
    <section className="overflow-hidden rounded-xl border border-rose-200 bg-white">
      <div className="flex items-center gap-3 bg-rose-50 px-5 py-3">
        <AlertTriangle className="h-5 w-5 shrink-0 text-rose-600" />
        <div>
          <h2 className="text-sm font-bold uppercase tracking-wide text-rose-900">Failures</h2>
          <p className="text-xs text-rose-700">
            {failureCount} {failureCount === 1 ? 'failure' : 'failures'} across {groups.length} scenario {groups.length === 1 ? 'run' : 'runs'}
          </p>
        </div>
      </div>
      <div className="divide-y divide-rose-100">
        {groups.map((group) => (
          <div key={group.runId} className="grid gap-2 px-5 py-3 md:grid-cols-[minmax(14rem,0.35fr)_minmax(0,1fr)]">
            <button
              type="button"
              onClick={() => onNavigate({
                runId: group.runId,
                runIndex: group.runIndex,
                targetId: group.runTargetId,
              })}
              className="flex min-w-0 items-center gap-2 self-start text-left text-sm font-semibold text-rose-800 hover:text-rose-950 focus:outline-none focus-visible:underline"
            >
              <CornerDownRight className="h-4 w-4 shrink-0" />
              <span className="truncate font-mono">{group.scenarioSlug}</span>
              <span className="shrink-0 text-xs font-medium text-rose-600">Run #{group.runIndex + 1}</span>
            </button>
            <div className="flex flex-wrap gap-2">
              {group.tasks.map((task) => (
                <button
                  key={task.id}
                  type="button"
                  onClick={() => onNavigate({
                    runId: group.runId,
                    runIndex: group.runIndex,
                    targetId: task.targetId,
                  })}
                  className="rounded-md bg-white px-2.5 py-1 text-left text-xs font-semibold text-rose-800 ring-1 ring-rose-200 hover:bg-rose-50 focus:outline-none focus:ring-2 focus:ring-rose-500"
                >
                  #{task.sequence} {task.name}
                  <span className="ml-1 uppercase text-rose-500">{task.status}</span>
                </button>
              ))}
              {group.isRunError && group.tasks.length === 0 ? (
                <button
                  type="button"
                  onClick={() => onNavigate({
                    runId: group.runId,
                    runIndex: group.runIndex,
                    targetId: group.runTargetId,
                  })}
                  className="rounded-md bg-white px-2.5 py-1 text-xs font-semibold text-rose-800 ring-1 ring-rose-200 hover:bg-rose-50 focus:outline-none focus:ring-2 focus:ring-rose-500"
                >
                  Execution errored
                </button>
              ) : null}
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}
