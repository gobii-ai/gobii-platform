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
type ChurnKeyStep = {
  stepType?: string
  header?: string
  description?: string
  offer?: {
    offerType?: string
  }
}
type ChurnKeyAcceptedOffer = {
  offerType?: string
  pauseDuration?: number
  trialExtensionDays?: number
  newPlanId?: string
  redirectUrl?: string
  couponId?: string
  couponType?: string
  couponAmount?: number
  couponDuration?: number
}
type ChurnKeySessionResults = {
  result?: string
  mode?: string
  surveyResponse?: string
  followupQuestion?: string
  followupResponse?: string
  feedback?: string
  usedClickToCancel?: boolean
  acceptedOffer?: ChurnKeyAcceptedOffer
}
type ChurnKeyInitOptions = {
  appId: string
  customerId: string
  authHash: string
  subscriptionId?: string
  mode: ChurnKeyMode
  provider: ChurnKeyProvider
  record?: boolean
  handlePause?: (customer: unknown, data: { pauseDuration: number }) => Promise<unknown>
  handleCancel?: (
    customer: unknown,
    surveyResponse?: string,
    feedback?: string | null,
    followupResponse?: unknown,
  ) => Promise<unknown>
  handleDiscount?: (customer: unknown, coupon?: unknown) => Promise<unknown>
  handleTrialExtension?: (customer: unknown, data: { trialExtensionDays: number }) => Promise<unknown>
  handlePlanChange?: (customer: unknown, data: { plan?: unknown }) => Promise<unknown>
  handleRedirect?: (customer: unknown, data: { redirectLabel?: string; redirectUrl?: string }) => Promise<unknown>
  handleSupportRequest?: (customer: unknown) => void
  onGoToAccount?: (sessionResults?: ChurnKeySessionResults) => void
  onStepChange?: (newStep?: ChurnKeyStep, oldStep?: ChurnKeyStep) => void
  onClose?: (sessionResults?: ChurnKeySessionResults) => void
  onCancel?: (customer?: unknown, surveyResponse?: string) => void
  onPause?: (customer?: unknown, data?: { pauseDuration?: number }) => void
  onDiscount?: (customer?: unknown, coupon?: unknown) => void
  onPlanChange?: (customer?: unknown, data?: { planId?: string }) => void
  onTrialExtension?: (customer?: unknown, data?: { trialExtensionDays?: number }) => void
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
