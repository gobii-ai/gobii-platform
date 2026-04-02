/**
 * Global type declarations for third-party scripts loaded via Django templates.
 */

/**
 * Segment Analytics.js API
 * Loaded via templates/base.html
 * @see https://segment.com/docs/connections/sources/catalog/libraries/website/javascript/
 */
interface SegmentAnalytics {
  /**
   * Track an event with optional properties.
   * @param event - The name of the event (e.g., 'Button Clicked')
   * @param properties - Optional properties associated with the event
   */
  track(event: string, properties?: Record<string, unknown>): void

  /**
   * Identify a user with optional traits.
   * Note: User identification is handled in base.html via Django context.
   * @param userId - The unique identifier for the user
   * @param traits - Optional traits associated with the user
   */
  identify(userId: string, traits?: Record<string, unknown>): void

  /**
   * Record a page view with optional category, name, and properties.
   * Note: Page tracking is handled in base.html via Django context.
   */
  page(category?: string, name?: string, properties?: Record<string, unknown>): void

  /**
   * Register a callback to be called when analytics is ready.
   * @param callback - Function to call when ready
   */
  ready(callback: () => void): void
}

type GtagParams = Record<string, string | number | boolean | undefined>
type GtagCommand = 'config' | 'event' | 'js' | 'set' | 'consent'
type Gtag = (command: GtagCommand, targetOrValue: string | Date, params?: GtagParams) => void
type GobiiTrackCtaPayload = {
  cta_id: string
  intent?: string
  destination?: string
  cta_label?: string
  source_page?: string
  page_slug?: string
  placement?: string
  cta_type?: string
}
type GobiiTrackCta = (payload: GobiiTrackCtaPayload) => void
type ChurnKeyMode = 'live' | 'test'
type ChurnKeyProvider = 'stripe'
type ChurnKeyInitOptions = {
  appId: string
  customerId: string
  authHash: string
  subscriptionId?: string
  mode: ChurnKeyMode
  provider: ChurnKeyProvider
  record?: boolean
  onClose?: () => void
  onCancel?: (customer?: unknown) => void
  onPause?: (customer?: unknown) => void
  onDiscount?: (customer?: unknown) => void
  onPlanChange?: (customer?: unknown) => void
  onTrialExtension?: (customer?: unknown) => void
  onError?: (error: unknown, type?: string) => void
}
type ChurnKeyGlobal = {
  created?: boolean
  init?: (action: 'show', options: ChurnKeyInitOptions) => void
}

declare global {
  interface Window {
    analytics?: SegmentAnalytics
    gtag?: Gtag
    gobiiTrackCta?: GobiiTrackCta
    churnkey?: ChurnKeyGlobal
  }
}

export {}
