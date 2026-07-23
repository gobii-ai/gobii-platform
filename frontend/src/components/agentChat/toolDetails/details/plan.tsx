import { Check, Circle, CircleDot, FileText, MessageSquareText, type LucideIcon } from 'lucide-react'

import { isRecord, parseResultObject } from '../../../../util/objectUtils'
import type { PlanTaskStatus } from '../../PlanTaskItem'
import type { ToolDetailProps } from '../../tooling/types'
import { Section } from '../shared'
import { isNonEmptyString } from '../utils'

type PlanStep = { step: string; status: PlanTaskStatus }
type Deliverable = [key: string, label: string, detail: string | null, icon: LucideIcon]

const STATUS_DISPLAY = {
  todo: [Circle, 'To do', 'text-slate-400', 'text-slate-700'],
  doing: [CircleDot, 'In progress', 'text-blue-600', 'font-semibold text-slate-900'],
  done: [Check, 'Done', 'rounded-full bg-emerald-100 p-0.5 text-emerald-700', 'text-slate-500'],
} satisfies Record<PlanTaskStatus, [LucideIcon, string, string, string]>

function text(value: unknown): string | null {
  return isNonEmptyString(value) ? value.trim() : null
}

function parsePlanSteps(value: unknown): PlanStep[] {
  if (!Array.isArray(value)) return []
  return value.flatMap((item) => {
    if (!isRecord(item)) return []
    const step = text(item.step)
    const status = item.status
    return step && (status === 'todo' || status === 'doing' || status === 'done')
      ? [{ step, status }]
      : []
  })
}

function parseDeliverables(parameters: Record<string, unknown> | null): Deliverable[] {
  if (!parameters) return []
  const files: Deliverable[] = Array.isArray(parameters.files)
    ? parameters.files.flatMap((item, index) => {
      if (!isRecord(item)) return []
      const path = text(item.path)
      return path
        ? [[`file:${path}:${index}`, text(item.label) ?? path.split('/').filter(Boolean).at(-1) ?? 'File', path, FileText]]
        : []
    })
    : []
  const messages: Deliverable[] = Array.isArray(parameters.messages)
    ? parameters.messages.flatMap((item, index) => {
      if (!isRecord(item)) return []
      const messageId = text(item.message_id)
      return messageId
        ? [[`message:${messageId}:${index}`, text(item.label) ?? 'Delivered message', null, MessageSquareText]]
        : []
    })
    : []
  return [...files, ...messages]
}

function buildPlanSummary(steps: PlanStep[]): string {
  if (!steps.length) return 'No active steps'
  const count = (status: PlanTaskStatus) => steps.filter((step) => step.status === status).length
  const [doing, todo, done] = [count('doing'), count('todo'), count('done')]
  return [
    doing ? `${doing} in progress` : null,
    todo ? `${todo} to do` : null,
    done ? `${done} done` : null,
  ].filter(Boolean).join(' • ')
}

export function UpdatePlanDetail({ entry }: ToolDetailProps) {
  const steps = parsePlanSteps(entry.parameters?.plan)
  const deliverables = parseDeliverables(entry.parameters)
  const result = parseResultObject(entry.result)
  const failed = entry.status === 'error' || result?.status === 'error'

  if (failed) {
    return (
      <div className="rounded-xl border border-red-200 bg-red-50 px-3 py-2.5 text-sm text-red-700">
        {text(result?.message) ?? 'The plan could not be updated.'}
      </div>
    )
  }

  return (
    <div className="space-y-4 text-sm">
      <p className="text-slate-500">{buildPlanSummary(steps)}</p>
      <Section title="Current Plan">
        {steps.length ? (
          <ol className="space-y-3">
            {steps.map((step, index) => {
              const [StatusIcon, label, iconClassName, titleClassName] = STATUS_DISPLAY[step.status]
              return (
                <li key={`${step.step}:${index}`} className="flex items-start gap-3">
                  <StatusIcon className={`mt-0.5 h-5 w-5 shrink-0 ${iconClassName}`} aria-hidden="true" />
                  <div className="min-w-0">
                    <p className={`leading-5 ${titleClassName}`}>{step.step}</p>
                    <p className="mt-0.5 text-xs text-slate-500">{label}</p>
                  </div>
                </li>
              )
            })}
          </ol>
        ) : (
          <p className="text-slate-600">The active plan was cleared.</p>
        )}
      </Section>
      {deliverables.length ? (
        <Section title="Deliverables">
          <ul className="space-y-2.5">
            {deliverables.map(([key, label, detail, Icon]) => (
              <li key={key} className="flex items-start gap-3">
                <Icon className="mt-0.5 h-4 w-4 shrink-0 text-indigo-600" aria-hidden="true" />
                <div className="min-w-0">
                  <p className="font-medium text-slate-700">{label}</p>
                  {detail ? <p className="mt-0.5 break-all font-mono text-xs text-slate-500">{detail}</p> : null}
                </div>
              </li>
            ))}
          </ul>
        </Section>
      ) : null}
    </div>
  )
}
