import {
  Check,
  Circle,
  CircleDot,
  FileText,
  MessageSquareText,
  type LucideIcon,
} from 'lucide-react'

import { isRecord } from '../../../../util/objectUtils'
import type { ToolDetailProps } from '../../tooling/types'
import { tryParseJson } from '../normalize'
import { Section } from '../shared'

type PlanStatus = 'todo' | 'doing' | 'done'

type PlanStep = {
  step: string
  status: PlanStatus
}

type PlanDeliverable = {
  key: string
  label: string
  detail: string | null
  icon: LucideIcon
}

const PLAN_STATUS_DISPLAY: Record<PlanStatus, {
  label: string
  icon: LucideIcon
  iconClassName: string
  titleClassName: string
}> = {
  todo: {
    label: 'To do',
    icon: Circle,
    iconClassName: 'text-slate-400',
    titleClassName: 'text-slate-700',
  },
  doing: {
    label: 'In progress',
    icon: CircleDot,
    iconClassName: 'text-blue-600',
    titleClassName: 'font-semibold text-slate-900',
  },
  done: {
    label: 'Done',
    icon: Check,
    iconClassName: 'rounded-full bg-emerald-100 p-0.5 text-emerald-700',
    titleClassName: 'text-slate-500',
  },
}

function nonEmptyString(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null
}

function parsePlanSteps(value: unknown): PlanStep[] {
  if (!Array.isArray(value)) return []

  return value.flatMap((item) => {
    if (!isRecord(item)) return []
    const step = nonEmptyString(item.step)
    const status = item.status
    if (!step || (status !== 'todo' && status !== 'doing' && status !== 'done')) {
      return []
    }
    return [{ step, status }]
  })
}

function parseDeliverables(parameters: Record<string, unknown> | null): PlanDeliverable[] {
  if (!parameters) return []

  const files = Array.isArray(parameters.files)
    ? parameters.files.flatMap((item, index) => {
      if (!isRecord(item)) return []
      const path = nonEmptyString(item.path)
      if (!path) return []
      return [{
        key: `file:${path}:${index}`,
        label: nonEmptyString(item.label) ?? path.split('/').filter(Boolean).at(-1) ?? 'File',
        detail: path,
        icon: FileText,
      }]
    })
    : []

  const messages = Array.isArray(parameters.messages)
    ? parameters.messages.flatMap((item, index) => {
      if (!isRecord(item)) return []
      const messageId = nonEmptyString(item.message_id)
      if (!messageId) return []
      return [{
        key: `message:${messageId}:${index}`,
        label: nonEmptyString(item.label) ?? 'Delivered message',
        detail: null,
        icon: MessageSquareText,
      }]
    })
    : []

  return [...files, ...messages]
}

function parseResultRecord(result: unknown): Record<string, unknown> | null {
  if (isRecord(result)) return result
  if (typeof result !== 'string') return null
  const parsed = tryParseJson(result)
  return isRecord(parsed) ? parsed : null
}

function buildPlanSummary(steps: PlanStep[]): string {
  if (!steps.length) return 'No active steps'

  const counts = steps.reduce(
    (summary, step) => {
      summary[step.status] += 1
      return summary
    },
    { todo: 0, doing: 0, done: 0 },
  )

  return [
    counts.doing ? `${counts.doing} in progress` : null,
    counts.todo ? `${counts.todo} to do` : null,
    counts.done ? `${counts.done} done` : null,
  ].filter(Boolean).join(' • ')
}

export function UpdatePlanDetail({ entry }: ToolDetailProps) {
  const parameters = entry.parameters
  const steps = parsePlanSteps(parameters?.plan)
  const deliverables = parseDeliverables(parameters)
  const result = parseResultRecord(entry.result)
  const failed = entry.status === 'error' || result?.status === 'error'
  const errorMessage = nonEmptyString(result?.message)

  if (failed) {
    return (
      <div className="rounded-xl border border-red-200 bg-red-50 px-3 py-2.5 text-sm text-red-700">
        {errorMessage ?? 'The plan could not be updated.'}
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
              const display = PLAN_STATUS_DISPLAY[step.status]
              const StatusIcon = display.icon
              return (
                <li key={`${step.step}:${index}`} className="flex items-start gap-3">
                  <StatusIcon
                    className={`mt-0.5 h-5 w-5 shrink-0 ${display.iconClassName}`}
                    aria-hidden="true"
                  />
                  <div className="min-w-0">
                    <p className={`leading-5 ${display.titleClassName}`}>{step.step}</p>
                    <p className="mt-0.5 text-xs text-slate-500">{display.label}</p>
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
            {deliverables.map((deliverable) => {
              const DeliverableIcon = deliverable.icon
              return (
                <li key={deliverable.key} className="flex items-start gap-3">
                  <DeliverableIcon className="mt-0.5 h-4 w-4 shrink-0 text-indigo-600" aria-hidden="true" />
                  <div className="min-w-0">
                    <p className="font-medium text-slate-700">{deliverable.label}</p>
                    {deliverable.detail ? (
                      <p className="mt-0.5 break-all font-mono text-xs text-slate-500">{deliverable.detail}</p>
                    ) : null}
                  </div>
                </li>
              )
            })}
          </ul>
        </Section>
      ) : null}
    </div>
  )
}
