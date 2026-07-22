let lastClientStateOrder = 0

/**
 * Returns an order marker whose wall-clock component can be captured when a
 * request starts while still ordering multiple observations in one millisecond.
 */
export function nextClientStateOrder(now = Date.now()): number {
  lastClientStateOrder = Math.max(lastClientStateOrder + 1, now * 1000)
  return lastClientStateOrder
}
