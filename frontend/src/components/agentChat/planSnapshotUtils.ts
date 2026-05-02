import type {
  AgentMessage,
  PlanFileDeliverable,
  PlanMessageDeliverable,
  PlanSnapshot,
  TimelineEvent,
} from '../../types/agentChat'

function planTitlesByStatus(plan: PlanSnapshot | null | undefined): Record<'todo' | 'doing' | 'done', Set<string>> {
  return {
    todo: new Set(plan?.todoTitles ?? []),
    doing: new Set(plan?.doingTitles ?? []),
    done: new Set(plan?.doneTitles ?? []),
  }
}

export function filterChangedPlanSnapshot(
  previous: PlanSnapshot | null | undefined,
  current: PlanSnapshot | null | undefined,
): PlanSnapshot | null {
  if (!current) {
    return null
  }

  const previousTitles = planTitlesByStatus(previous)
  const currentRows = {
    todo: current.todoTitles ?? [],
    doing: current.doingTitles ?? [],
    done: current.doneTitles ?? [],
  }
  const changedTodoTitles = currentRows.todo.filter((title) => !previousTitles.todo.has(title))
  const changedDoingTitles = currentRows.doing.filter((title) => !previousTitles.doing.has(title))
  const changedDoneTitles = currentRows.done.filter((title) => !previousTitles.done.has(title))

  const hasStepChange = changedTodoTitles.length > 0 || changedDoingTitles.length > 0 || changedDoneTitles.length > 0

  const previousFileKeys = new Set((previous?.files ?? []).map((file) => `${file.path}:${file.downloadUrl ?? ''}`))
  const changedFiles: PlanFileDeliverable[] = (current.files ?? []).filter((file) => (
    !previousFileKeys.has(`${file.path}:${file.downloadUrl ?? ''}`)
  ))
  const previousMessageIds = new Set((previous?.messages ?? []).map((message) => message.messageId))
  const changedMessages: PlanMessageDeliverable[] = (current.messages ?? []).filter((message) => (
    !previousMessageIds.has(message.messageId)
  ))

  if (!hasStepChange && changedFiles.length === 0 && changedMessages.length === 0) {
    return null
  }

  return {
    todoCount: changedTodoTitles.length,
    doingCount: changedDoingTitles.length,
    doneCount: changedDoneTitles.length,
    todoTitles: changedTodoTitles,
    doingTitles: changedDoingTitles,
    doneTitles: changedDoneTitles,
    files: changedFiles,
    messages: changedMessages,
  }
}

export function inferPlanFilesFromMessageAttachments(
  plan: PlanSnapshot | null | undefined,
  events: TimelineEvent[],
): PlanFileDeliverable[] {
  if (!plan || (plan.files?.length ?? 0) > 0 || !plan.messages?.length) {
    return plan?.files ?? []
  }

  const messagesById = new Map<string, AgentMessage>()
  for (const event of events) {
    if (event.kind === 'message') {
      messagesById.set(event.message.id, event.message)
    }
  }

  const inferredFiles: PlanFileDeliverable[] = []
  const seenKeys = new Set<string>()
  for (const messageDeliverable of plan.messages) {
    const messageEvent = messagesById.get(messageDeliverable.messageId)
    for (const attachment of messageEvent?.attachments ?? []) {
      const downloadUrl = attachment.downloadUrl ?? attachment.url ?? null
      const path = attachment.filespacePath ?? downloadUrl ?? attachment.filename
      const key = attachment.filespaceNodeId ?? path
      if (!path || seenKeys.has(key)) {
        continue
      }
      seenKeys.add(key)
      inferredFiles.push({
        path,
        label: attachment.filename,
        downloadUrl,
      })
    }
  }

  return inferredFiles
}

export function addInferredPlanFiles(plan: PlanSnapshot | null | undefined, events: TimelineEvent[]): PlanSnapshot | null {
  if (!plan || (plan.files?.length ?? 0) > 0) {
    return plan ?? null
  }

  const inferredFiles = inferPlanFilesFromMessageAttachments(plan, events)
  if (!inferredFiles.length) {
    return plan
  }

  return {
    ...plan,
    files: inferredFiles,
  }
}

export function hasCompletedPlanDeliverables(plan: PlanSnapshot | null | undefined): boolean {
  if (!plan) {
    return false
  }
  const total = plan.todoCount + plan.doingCount + plan.doneCount
  const deliverableCount = (plan.files?.length ?? 0) + (plan.messages?.length ?? 0)
  return total > 0 && plan.todoCount === 0 && plan.doingCount === 0 && plan.doneCount === total && deliverableCount > 0
}
