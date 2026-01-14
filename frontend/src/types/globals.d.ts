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

declare global {
  interface Window {
    analytics?: SegmentAnalytics
  }
}

export {}
