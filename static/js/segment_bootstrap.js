(function () {
  function ensureStub() {
    window.analytics = window.analytics || {};
    if (typeof window.analytics.page !== 'function') {
      window.analytics.page = function () {};
    }
    if (typeof window.analytics.track !== 'function') {
      window.analytics.track = function () {};
    }
    if (typeof window.analytics.identify !== 'function') {
      window.analytics.identify = function () {};
    }
    if (typeof window.analytics.ready !== 'function') {
      window.analytics.ready = function (callback) {
        if (typeof callback === 'function') {
          callback();
        }
      };
    }

    return window.analytics;
  }

  function bootstrapSnippet() {
    var analytics = window.analytics = window.analytics || [];

    if (analytics.initialize) {
      return analytics;
    }

    if (analytics.invoked) {
      if (window.console && window.console.error) {
        window.console.error('Segment snippet included twice.');
      }
      return analytics;
    }

    analytics.invoked = true;
    analytics.methods = [
      'trackSubmit', 'trackClick', 'trackLink', 'trackForm', 'pageview',
      'identify', 'reset', 'group', 'track', 'ready', 'alias', 'debug',
      'page', 'screen', 'once', 'off', 'on', 'addSourceMiddleware',
      'addIntegrationMiddleware', 'setAnonymousId', 'addDestinationMiddleware',
      'register'
    ];

    analytics.factory = function (methodName) {
      return function () {
        if (window.analytics.initialized) {
          return window.analytics[methodName].apply(window.analytics, arguments);
        }

        var args = Array.prototype.slice.call(arguments);
        if (['track', 'screen', 'alias', 'group', 'page', 'identify'].indexOf(methodName) > -1) {
          var canonicalLink = document.querySelector("link[rel='canonical']");
          args.push({
            __t: 'bpc',
            c: canonicalLink && canonicalLink.getAttribute('href') || void 0,
            p: window.location.pathname,
            u: window.location.href,
            s: window.location.search,
            t: document.title,
            r: document.referrer
          });
        }
        args.unshift(methodName);
        analytics.push(args);
        return analytics;
      };
    };

    for (var index = 0; index < analytics.methods.length; index += 1) {
      var method = analytics.methods[index];
      analytics[method] = analytics.factory(method);
    }

    analytics.load = function (writeKey, options) {
      var script = document.createElement('script');
      script.type = 'text/javascript';
      script.async = true;
      script.setAttribute('data-global-segment-analytics-key', 'analytics');
      script.src = 'https://cdn.segment.com/analytics.js/v1/' + writeKey + '/analytics.min.js';
      var firstScript = document.getElementsByTagName('script')[0];
      firstScript.parentNode.insertBefore(script, firstScript);
      analytics._loadOptions = options;
    };

    analytics.SNIPPET_VERSION = '5.2.0';
    return analytics;
  }

  function installDefaultMiddleware(defaultProperties) {
    if (!defaultProperties || window.__gobiiSegmentDefaultMiddlewareInstalled) {
      return;
    }

    if (!window.analytics || typeof window.analytics.addSourceMiddleware !== 'function') {
      return;
    }

    window.analytics.addSourceMiddleware(function (_ref) {
      var payload = _ref.payload;
      var next = _ref.next;
      var obj = payload && payload.obj ? payload.obj : {};
      var properties = obj.properties || {};

      Object.keys(defaultProperties).forEach(function (key) {
        var value = defaultProperties[key];
        if (value !== undefined) {
          properties[key] = value;
        }
      });

      obj.properties = properties;
      payload.obj = obj;
      next(payload);
    });

    window.__gobiiSegmentDefaultMiddlewareInstalled = true;
  }

  function init(config) {
    config = config || {};
    var enabled = Boolean(config.enabled && config.writeKey);

    if (!enabled) {
      ensureStub();
      return false;
    }

    var analytics = bootstrapSnippet();
    if (!window.__gobiiSegmentLoadedKey) {
      analytics.load(config.writeKey);
      window.__gobiiSegmentLoadedKey = config.writeKey;
    }

    installDefaultMiddleware(config.defaultProperties || null);
    return true;
  }

  window.GobiiSegmentBootstrap = {
    init: init,
    ensureStub: ensureStub
  };
})();
