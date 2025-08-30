/**
 * Automatically detects and formats phone numbers on a webpage based on the user's region.
 * Targets elements with a specified CSS class and updates their text content or value to
 * display phone numbers in a formatted, region-specific manner.
 *
 * Global variable `window.__COUNTRY__` can override the detection of the user's region.
 * If not set, the script derives the region from the user's browser locale.
 *
 * The script operates in the following stages:
 * - Detects the user's region.
 * - Formats existing elements with the targeted CSS class on page load.
 * - Observes DOM changes to detect and format newly added elements dynamically.
 *
 * @constant CLASS - The targeted CSS class name used to identify elements for phone number formatting.
 *
 * @function detectRegion
 * Detects the region to use for phone number formatting.
 * Priority:
 * 1) Uses the `window.__COUNTRY__` global variable if it exists and matches a valid two-letter country code.
 * 2) Derives the region from the browser's `navigator.language`.
 * Returns 'US' by default if no valid region is detected.
 *
 * @function formatNode
 * Formats a given DOM node based on the user's region*/

(() => {
  const CLASS = 'phone-number-to-format';

  /* ---------- helpers ---------- */

  function detectRegion () {
    // 1) server-supplied country code wins
    if (typeof window.__COUNTRY__ === 'string' &&
        /^[A-Z]{2}$/.test(window.__COUNTRY__)) {
      return window.__COUNTRY__;
    }
    // 2) derive from browser locale
    const locale = navigator.language || 'en-US';
    const region = locale.split('-')[1];
    return region ? region.toUpperCase() : 'US';
  }

  function formatNode (node, region) {
    const raw = (node.textContent || node.value || '').trim();
    if (!raw) return;

    try {
      const pn = libphonenumber.parsePhoneNumber(raw, region);
      if (!pn) return;

      const formatted = pn.formatNational();

      if (node.nodeName === "INPUT" || node.nodeName === "TEXTAREA") {
        node.value = formatted;
      } else {
        node.textContent = formatted;     // visible text
        const telLink = node.closest('a[href^="tel:"]');  // keep click-to-call
        if (telLink) telLink.setAttribute('href', pn.getURI());
      }
    } catch (_) { /* silently ignore parsing errors */ }
  }

  function formatExisting (region) {
    document
      .querySelectorAll(`.${CLASS}`)
      .forEach(el => formatNode(el, region));
  }

  /* ---------- run on page load ---------- */

  const userRegion = detectRegion();

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded',
      () => formatExisting(userRegion),
      { once: true }
    );
  } else {
    formatExisting(userRegion);
  }

  /* ---------- watch for future DOM inserts ---------- */

  const observer = new MutationObserver(mList => {
    for (const m of mList) {
      m.addedNodes.forEach(node => {
        if (node.nodeType !== Node.ELEMENT_NODE) return;

        if (node.classList?.contains(CLASS)) {
          formatNode(node, userRegion);                  // direct match
        }

        node.querySelectorAll?.(`.${CLASS}`)
            .forEach(child => formatNode(child, userRegion)); // nested matches
      });
    }
  });

  observer.observe(document.body, { childList: true, subtree: true });
})();
