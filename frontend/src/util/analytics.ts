/**
 * Analytics utilities for tracking events via Segment.
 *
 * Segment is loaded globally via templates/base.html and handles:
 * - User identification (via Django auth context)
 * - Page tracking (via Django page metadata)
 *
 * This module provides typed helpers for event tracking in React components.
 *
 * @example
 * ```ts
 * import { track, AnalyticsEvent } from '../util/analytics'
 *
 * track(AnalyticsEvent.INSIGHT_VIEWED, { insightType: 'burn_rate' })
 * ```
 */

// Re-export events for convenient single import
export { AnalyticsEvent } from '../constants/analyticsEvents'
export type { AnalyticsEventType } from '../constants/analyticsEvents'

type TrackProperties = Record<string, unknown>

/**
 * Track an analytics event.
 * Safe to call even if analytics is not loaded (e.g., blocked by ad blocker).
 *
 * @param event - The event name (use snake_case by convention)
 * @param properties - Optional event properties
 *
 * @example
 * ```ts
 * track('insight_viewed', { insightType: 'burn_rate', insightId: '123' })
 * track('insight_dismissed', { insightType: 'time_saved' })
 * ```
 */
export function track(event: string, properties?: TrackProperties): void {
  window.analytics?.track(event, properties)
}

/**
 * Track an event only if a condition is true.
 * Useful for conditional tracking without cluttering component code.
 *
 * @param condition - Whether to track the event
 * @param event - The event name
 * @param properties - Optional event properties
 *
 * @example
 * ```ts
 * trackIf(isFirstView, 'insight_first_view', { insightType })
 * ```
 */
export function trackIf(condition: boolean, event: string, properties?: TrackProperties): void {
  if (condition) {
    track(event, properties)
  }
}

/**
 * Create a tracking function pre-bound to a specific event.
 * Useful when the same event is tracked in multiple places.
 *
 * @param event - The event name to bind
 * @returns A function that tracks the bound event with optional properties
 *
 * @example
 * ```ts
 * const trackInsightView = createTracker('insight_viewed')
 * trackInsightView({ insightType: 'burn_rate' })
 * ```
 */
export function createTracker(event: string): (properties?: TrackProperties) => void {
  return (properties?: TrackProperties) => track(event, properties)
}
