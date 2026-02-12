/* global analytics, fbq, gtag, lintrk, rdt, ttq */
(function () {
  function getUtmParams() {
    if (typeof window.getUTMParams === 'function') {
      return window.getUTMParams();
    }
    return {};
  }

  function trackWithSegment(eventName, properties) {
    if (window.analytics && typeof window.analytics.track === 'function') {
      window.analytics.track(eventName, properties);
    }
  }

  function firePixels(data, source) {
    var pixels = data.pixels || {};
    var value = data.registrationValue || 0;
    var currency = 'USD';
    var utmParams = getUtmParams();

    if (pixels.ga && typeof window.gtag === 'function') {
      gtag('event', 'sign_up', Object.assign({ method: 'email', value: value, currency: currency }, utmParams));
    }

    if (pixels.reddit && typeof window.rdt === 'function') {
      rdt('track', 'SignUp', {
        email: data.emailHash,
        externalId: data.idHash,
        conversionId: data.eventId || ('reg-' + data.idHash),
        value: value,
        currency: currency,
      });
    }

    if (pixels.tiktok && window.ttq && typeof window.ttq.track === 'function') {
      ttq.track('CompleteRegistration', {
        event_id: data.eventId,
        external_id: data.idHash,
        email: data.emailHash,
        value: value,
        currency: currency,
      });
    }

    if (pixels.meta && typeof window.fbq === 'function') {
      fbq('track', 'CompleteRegistration', Object.assign({
        value: value,
        currency: currency,
      }, utmParams), {
        external_id: data.idHash,
        em: data.emailHash,
        eventID: data.eventId,
      });
    }

    if (pixels.linkedin && typeof window.lintrk === 'function') {
      window.lintrk('track', { conversion_id: pixels.linkedin });
    }

    trackWithSegment('Signup Pixels Fired', {
      eventId: data.eventId,
      source: source,
      pixelsFired: Object.keys(pixels),
    });
  }

  function fetchAndFire(options) {
    options = options || {};
    var endpoint = options.endpoint || '/clear_signup_tracking';
    var source = options.source || 'unknown';
    var maxRetries = Number(options.maxRetries || 3);
    var baseDelayMs = Number(options.baseDelayMs || 1000);

    function fetchWithRetry(attempt) {
      fetch(endpoint, { credentials: 'same-origin' })
        .then(function (response) {
          if (!response.ok) {
            throw new Error('HTTP ' + response.status);
          }
          return response.json();
        })
        .then(function (payload) {
          if (payload.tracking) {
            firePixels(payload, source);
          }
        })
        .catch(function (error) {
          if (attempt < maxRetries) {
            var delay = baseDelayMs * Math.pow(2, attempt - 1);
            setTimeout(function () {
              fetchWithRetry(attempt + 1);
            }, delay);
            return;
          }

          trackWithSegment('Signup Pixel Fetch Failed', {
            error: error && error.message ? error.message : 'Unknown error',
            attempts: maxRetries,
            source: source,
          });
        });
    }

    fetchWithRetry(1);
  }

  window.GobiiSignupTracking = {
    fetchAndFire: fetchAndFire,
  };
})();
