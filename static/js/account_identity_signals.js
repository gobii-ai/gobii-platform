(function () {
  if (window.GobiiIdentitySignals) {
    return;
  }

  function normalizeGaClientId(value) {
    const normalized = (value || "").trim().replace(/^"+|"+$/g, "");
    const match = normalized.match(/^GA\d+\.\d+\.(\d+\.\d+)$/i);
    if (match) {
      return match[1];
    }
    return normalized;
  }

  function readCookie(name) {
    const prefix = `${name}=`;
    const cookie = document.cookie
      .split(";")
      .map((part) => part.trim())
      .find((part) => part.startsWith(prefix));
    if (!cookie) {
      return "";
    }
    return decodeURIComponent(cookie.slice(prefix.length));
  }

  function writeCookie(name, value, maxAge) {
    if (!value) {
      return;
    }
    let cookie = `${name}=${encodeURIComponent(value)}; Max-Age=${maxAge}; Path=/; SameSite=Lax`;
    if (window.location.protocol === "https:") {
      cookie += "; Secure";
    }
    document.cookie = cookie;
  }

  function deleteCookie(name) {
    let cookie = `${name}=; Max-Age=0; Path=/; SameSite=Lax`;
    if (window.location.protocol === "https:") {
      cookie += "; Secure";
    }
    document.cookie = cookie;
  }

  function clearStagedFpjsCookies() {
    deleteCookie("gobii_signup_fpjs_visitor_id");
    deleteCookie("gobii_signup_fpjs_request_id");
  }

  function withTimeout(promise, timeoutMs, fallbackValue) {
    let timerId = null;
    return Promise.race([
      promise,
      new Promise((resolve) => {
        timerId = window.setTimeout(() => resolve(fallbackValue), timeoutMs);
      }),
    ]).finally(() => {
      if (timerId !== null) {
        window.clearTimeout(timerId);
      }
    });
  }

  function createGaClientIdPromise(options) {
    const measurementId = options.measurementId;
    const onResolved = options.onResolved;

    return new Promise((resolve) => {
      const fallback = normalizeGaClientId(readCookie("_ga"));
      if (!measurementId || typeof window.gtag !== "function") {
        resolve(fallback);
        return;
      }

      let settled = false;
      const finish = (value) => {
        if (settled) {
          return;
        }
        settled = true;
        resolve(normalizeGaClientId(value) || fallback || "");
      };

      const timerId = window.setTimeout(() => finish(fallback), 1000);
      try {
        window.gtag("get", measurementId, "client_id", (value) => {
          window.clearTimeout(timerId);
          finish(value);
        });
      } catch (_error) {
        window.clearTimeout(timerId);
        finish(fallback);
      }
    }).then((gaClientId) => {
      const normalizedGaClientId = normalizeGaClientId(gaClientId);
      if (typeof onResolved === "function") {
        onResolved(normalizedGaClientId);
      }
      return normalizedGaClientId;
    });
  }

  function createFpjsPromise(options) {
    return withTimeout(
      import(options.loaderUrl)
        .then(({ load, defaultEndpoint }) => {
          const loadOptions = {};
          if (options.behaviorUrl) {
            loadOptions.endpoint = [`${options.behaviorUrl}?region=us`, defaultEndpoint];
          }
          return load(loadOptions);
        })
        .then((fpAgent) => fpAgent.get())
        .then((result) => {
          if (typeof options.onResolved === "function") {
            options.onResolved(result);
          }
          return result;
        })
        .catch(() => null),
      options.timeoutMs,
      null
    );
  }

  window.GobiiIdentitySignals = {
    clearStagedFpjsCookies,
    createFpjsPromise,
    createGaClientIdPromise,
    deleteCookie,
    normalizeGaClientId,
    readCookie,
    withTimeout,
    writeCookie,
  };
})();
