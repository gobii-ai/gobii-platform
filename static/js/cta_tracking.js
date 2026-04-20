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

  function getDataset(element) {
    if (!element || !element.dataset) {
      return {};
    }
    return element.dataset;
  }

  function buildBaseProps(element, options) {
    options = options || {};
    var dataset = getDataset(element);
    var pathname = options.page_path || window.location.pathname || '/';
    var pageSlug = options.page_slug || dataset.analyticsPageSlug || derivePageSlug(pathname);

    return {
      page_slug: pageSlug,
      medium: 'Web',
      page_path: pathname,
      placement: options.placement !== undefined ? options.placement : (dataset.analyticsPlacement || '')
    };
  }

  function buildDatasetProps(element, options) {
    options = options || {};
    var dataset = getDataset(element);

    return compactProperties({
      auth_provider: options.auth_provider !== undefined ? options.auth_provider : (dataset.analyticsAuthProvider || ''),
      auth_surface: options.auth_surface !== undefined ? options.auth_surface : (dataset.analyticsAuthSurface || '')
    });
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

  function getSubmitter(form, options) {
    options = options || {};
    if (options.submitter) {
      return options.submitter;
    }

    return form.querySelector('button[type="submit"], input[type="submit"]');
  }

  function buildFormProps(form, options) {
    options = options || {};
    var sourceInput = form.querySelector('input[name="source_page"]');
    var sourcePage = options.source_page !== undefined ? options.source_page : (sourceInput ? sourceInput.value : '');
    var ctaId = options.cta_id || form.dataset.analyticsCtaId || sourcePage;
    if (!ctaId) {
      return {};
    }

    var submitter = getSubmitter(form, options);
    var label = options.cta_label !== undefined ? options.cta_label : getElementLabel(submitter);
    var action = form.getAttribute('action') || '';
    var destination = normalizeDestination(
      options.destination !== undefined ? options.destination : (form.dataset.analyticsDestination || action)
    );
    var trialOnboardingTarget = options.trial_onboarding_target !== undefined
      ? options.trial_onboarding_target
      : ((form.querySelector('input[name="trial_onboarding_target"]') || {}).value || '');

    return compactProperties(Object.assign(
      {},
      buildBaseProps(form, options),
      {
        id: ctaId,
        cta_id: ctaId,
        source_page: sourcePage,
        intent: options.intent !== undefined ? options.intent : (form.dataset.analyticsIntent || ''),
        destination: destination,
        cta_label: label,
        cta_type: options.cta_type || 'form_submit',
        trial_onboarding_target: trialOnboardingTarget,
        authenticated: options.authenticated !== undefined ? options.authenticated : (form.dataset.authenticated || '')
      },
      buildDatasetProps(form, options),
      compactProperties(options.properties || {})
    ));
  }

  function buildClickProps(element, options) {
    options = options || {};
    var dataset = getDataset(element);
    var ctaId = options.cta_id || dataset.analyticsCtaId || '';
    if (!ctaId) {
      return {};
    }

    var label = options.cta_label !== undefined ? options.cta_label : (dataset.analyticsLabel || element.textContent || '').trim();
    var href = element.getAttribute('href') || '';
    var destination = normalizeDestination(
      options.destination !== undefined ? options.destination : (dataset.analyticsDestination || href)
    );

    return compactProperties(Object.assign(
      {},
      buildBaseProps(element, options),
      {
        id: ctaId,
        cta_id: ctaId,
        intent: options.intent !== undefined ? options.intent : (dataset.analyticsIntent || ''),
        destination: destination,
        cta_label: label,
        cta_type: options.cta_type || 'click'
      },
      buildDatasetProps(element, options),
      compactProperties(options.properties || {})
    ));
  }

  function buildCtaTrackingProperties(element, options) {
    options = options || {};
    if (!element) {
      return compactProperties(options.properties || {});
    }

    var tagName = element.tagName ? element.tagName.toLowerCase() : '';
    if (tagName === 'form' || options.is_form === true) {
      return buildFormProps(element, options);
    }

    return buildClickProps(element, options);
  }

  function trackCtaElement(element, options) {
    var properties = buildCtaTrackingProperties(element, options);
    if (!properties.cta_id) {
      return;
    }

    track(options && options.eventName ? options.eventName : getEventName(), properties);
    return properties;
  }

  function trackFormSubmit(form, submitEvent) {
    return trackCtaElement(form, {
      submitter: submitEvent && submitEvent.submitter ? submitEvent.submitter : null
    });
  }

  function trackClick(element) {
    return trackCtaElement(element);
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

  window.gobiiGetCtaTrackingProperties = buildCtaTrackingProperties;
  window.gobiiTrackCtaElement = trackCtaElement;
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
      page_path: payload.page_path || window.location.pathname || '/',
      placement: payload.placement,
      cta_type: payload.cta_type || 'manual',
      medium: 'Web',
      auth_provider: payload.auth_provider,
      auth_surface: payload.auth_surface
    });

    properties = Object.assign(properties, compactProperties(payload.properties || {}));
    track(getEventName(), properties);
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bindTracking);
    return;
  }

  bindTracking();
})();
