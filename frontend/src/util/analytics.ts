export { AnalyticsEvent } from '../constants/analyticsEvents'
export type { AnalyticsEventType } from '../constants/analyticsEvents'

type TrackProperties = Record<string, unknown>
export type AnalyticsBillingContext = Record<string, unknown>

export function applyAnalyticsBillingContext(context: AnalyticsBillingContext | null | undefined): void {
  if (context && Object.keys(context).length > 0) {
    window.GobiiSegmentBootstrap?.setDefaultProperties?.(context)
  }
}

export function track(event: string, properties?: TrackProperties): void {
  window.analytics?.track(event, properties)
}

export function trackIf(condition: boolean, event: string, properties?: TrackProperties): void {
  if (condition) {
    track(event, properties)
  }
}

export function createTracker(event: string): (properties?: TrackProperties) => void {
  return (properties?: TrackProperties) => track(event, properties)
}
