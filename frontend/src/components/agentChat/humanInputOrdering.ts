import type { PendingHumanInputRequest } from '../../types/agentChat'

export function orderHumanInputRequests(
  requests: PendingHumanInputRequest[],
): PendingHumanInputRequest[] {
  const batchOrder = new Map<string, number>()
  requests.forEach((request, index) => {
    if (!batchOrder.has(request.batchId)) {
      batchOrder.set(request.batchId, index)
    }
  })

  return [...requests].sort((left, right) => {
    const leftBatchOrder = batchOrder.get(left.batchId) ?? 0
    const rightBatchOrder = batchOrder.get(right.batchId) ?? 0
    if (leftBatchOrder !== rightBatchOrder) {
      return leftBatchOrder - rightBatchOrder
    }
    return left.batchPosition - right.batchPosition
  })
}
