(function () {
  function derivePageSlug(pathname) {
    if (!pathname || pathname === '/') {
      return 'home';
    }

    return pathname
      .replace(/^\/+|\/+$/g, '')
      .replace(/[\/-]+/g, '_')
      .replace(/[^a-zA-Z0-9_]+/g, '_')
      .replace(/_+/g, '_')
      .toLowerCase();
  }

  function normalizeDestination(rawDestination) {
    if (!rawDestination) {
      return '';
    }

    if (rawDestination.charAt(0) === '#') {
      return (window.location.pathname || '/') + rawDestination;
    }

    try {
      var parsed = new URL(rawDestination, window.location.href);
      return parsed.pathname + parsed.search + parsed.hash;
    } catch (error) {
      return rawDestination;
    }
  }

  function compactProperties(props) {
    var compacted = {};
    Object.keys(props).forEach(function (key) {
      var value = props[key];
      if (value === undefined || value === null || value === '') {
        return;
      }
      compacted[key] = value;
    });
    return compacted;
  }

  function track(eventName, properties) {
    if (!window.analytics || !window.analytics.track) {
      return;
    }
    window.analytics.track(eventName, properties);
  }

  function getEventName() {
    var fromDataset = document.body ? document.body.dataset.analyticsCtaEvent : '';
    return fromDataset || 'CTA Clicked';
  }

  function buildBaseProps(element) {
    var pathname = window.location.pathname || '/';
    var pageSlug = element.dataset.analyticsPageSlug || derivePageSlug(pathname);

    return {
      page_slug: pageSlug,
      medium: 'Web',
      page_path: pathname,
      placement: element.dataset.analyticsPlacement || ''
    };
  }

  function getElementLabel(element) {
    if (!element) {
      return '';
    }

    if (typeof element.value === 'string' && element.value.trim()) {
      return element.value.trim();
    }

    return (element.textContent || '').trim();
  }

  function trackFormSubmit(form, submitEvent) {
    var sourceInput = form.querySelector('input[name="source_page"]');
    var sourcePage = sourceInput ? sourceInput.value : '';
    var ctaId = form.dataset.analyticsCtaId || sourcePage;
    if (!ctaId) {
      return;
    }

    var submitter = submitEvent && submitEvent.submitter ? submitEvent.submitter : null;
    if (!submitter) {
      submitter = form.querySelector('button[type="submit"], input[type="submit"]');
    }

    var label = getElementLabel(submitter);
    var action = form.getAttribute('action') || '';
    var destination = normalizeDestination(form.dataset.analyticsDestination || action);

    var properties = compactProperties({
      id: ctaId,
      cta_id: ctaId,
      source_page: sourcePage,
      intent: form.dataset.analyticsIntent || '',
      destination: destination,
      cta_label: label,
      cta_type: 'form_submit',
      trial_onboarding_target: (form.querySelector('input[name="trial_onboarding_target"]') || {}).value || '',
      authenticated: form.dataset.authenticated || ''
    });

    track(getEventName(), Object.assign({}, buildBaseProps(form), properties));
  }

  function trackClick(element) {
    var ctaId = element.dataset.analyticsCtaId || '';
    if (!ctaId) {
      return;
    }

    var label = (element.dataset.analyticsLabel || element.textContent || '').trim();
    var href = element.getAttribute('href') || '';
    var destination = normalizeDestination(element.dataset.analyticsDestination || href);

    var properties = compactProperties({
      id: ctaId,
      cta_id: ctaId,
      intent: element.dataset.analyticsIntent || '',
      destination: destination,
      cta_label: label,
      cta_type: 'click'
    });

    track(getEventName(), Object.assign({}, buildBaseProps(element), properties));
  }

  function isTrackedPage() {
    var enabled = document.body ? document.body.dataset.analyticsCtaTrackingEnabled : '';
    if (enabled === 'true') {
      return true;
    }
    if (enabled === 'false') {
      return false;
    }

    var path = window.location.pathname || '/';
    return path === '/' || path.indexOf('/solutions/') === 0;
  }

  function bindTracking() {
    if (!isTrackedPage()) {
      return;
    }

    var allForms = document.querySelectorAll('form');
    allForms.forEach(function (form) {
      if (!form.querySelector('input[name="source_page"]') && !form.dataset.analyticsCtaId) {
        return;
      }

      form.addEventListener('submit', function (event) {
        trackFormSubmit(form, event);
      });
    });

    var clickCtas = document.querySelectorAll('[data-analytics-cta-id]:not(form)');
    clickCtas.forEach(function (element) {
      element.addEventListener('click', function () {
        trackClick(element);
      });
    });
  }

  window.gobiiTrackCta = function (payload) {
    if (!payload || !payload.cta_id) {
      return;
    }

    var properties = compactProperties({
      id: payload.cta_id,
      cta_id: payload.cta_id,
      intent: payload.intent,
      destination: payload.destination,
      cta_label: payload.cta_label,
      source_page: payload.source_page,
      page_slug: payload.page_slug,
      placement: payload.placement,
      cta_type: payload.cta_type || 'manual',
      medium: 'Web'
    });

    track(getEventName(), properties);
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bindTracking);
    return;
  }

  bindTracking();
})();
