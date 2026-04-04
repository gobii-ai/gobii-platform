(function () {
  if (window.GobiiStripeRadar) {
    return;
  }

  const configNode = document.getElementById("stripe-radar-config");
  let config = null;
  if (configNode && configNode.textContent) {
    try {
      config = JSON.parse(configNode.textContent);
    } catch (error) {
      config = null;
    }
  }

  const navigateTo = (url, target) => {
    if (!url) {
      return;
    }
    if (target && target !== "_self") {
      window.open(url, target);
      return;
    }
    window.location.href = url;
  };

  const submitForm = (form) => {
    if (!form) {
      return;
    }
    HTMLFormElement.prototype.submit.call(form);
  };

  const api = {
    available: false,
    ensureSession: async function () {
      return null;
    },
    captureThenNavigate: async function (url, options) {
      const target = options && options.target ? options.target : "_self";
      navigateTo(url, target);
    },
    captureThenSubmit: async function (form) {
      submitForm(form);
    },
    bind: function () {},
  };

  if (
    !config
    || !config.publishableKey
    || !config.captureUrl
    || typeof window.Stripe !== "function"
  ) {
    window.GobiiStripeRadar = api;
    return;
  }

  const stripe = window.Stripe(config.publishableKey);
  if (!stripe || typeof stripe.createRadarSession !== "function") {
    window.GobiiStripeRadar = api;
    return;
  }

  let persistedRadarSessionId = "";
  let radarSessionPromise = null;

  const persistRadarSession = async (radarSessionId) => {
    if (!radarSessionId || radarSessionId === persistedRadarSessionId) {
      return;
    }

    persistedRadarSessionId = radarSessionId;
    try {
      await fetch(config.captureUrl, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": typeof window.getCsrfTokenValue === "function" ? window.getCsrfTokenValue() : "",
        },
        body: JSON.stringify({
          radarSessionId: radarSessionId,
        }),
      });
    } catch (error) {
      persistedRadarSessionId = radarSessionId;
    }
  };

  const waitForRadarSession = async () => {
    if (!radarSessionPromise) {
      radarSessionPromise = stripe.createRadarSession()
        .then(async (result) => {
          const radarSessionId = result && result.radarSession && result.radarSession.id
            ? result.radarSession.id
            : "";
          if (!radarSessionId) {
            return null;
          }
          await persistRadarSession(radarSessionId);
          return radarSessionId;
        })
        .catch(function () {
          return null;
        });
    }
    return radarSessionPromise;
  };

  const waitWithTimeout = async () => {
    const timeoutPromise = new Promise((resolve) => {
      window.setTimeout(function () {
        resolve(null);
      }, 800);
    });
    return Promise.race([waitForRadarSession(), timeoutPromise]);
  };

  api.available = true;
  api.ensureSession = waitForRadarSession;
  api.captureThenNavigate = async function (url, options) {
    const target = options && options.target ? options.target : "_self";
    try {
      await waitWithTimeout();
    } catch (error) {
      // Best effort only. Navigation should continue.
    }
    navigateTo(url, target);
  };
  api.captureThenSubmit = async function (form) {
    if (!form) {
      return;
    }
    if (form.dataset.stripeRadarSubmitting === "true") {
      return;
    }
    form.dataset.stripeRadarSubmitting = "true";
    try {
      await waitWithTimeout();
    } catch (error) {
      // Best effort only. Submission should continue.
    }
    submitForm(form);
  };
  api.bind = function (root) {
    const scope = root || document;
    scope.querySelectorAll("[data-stripe-radar-link]").forEach(function (link) {
      if (link.dataset.stripeRadarBound === "true") {
        return;
      }
      link.dataset.stripeRadarBound = "true";
      link.addEventListener("click", function (event) {
        if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
          return;
        }
        event.preventDefault();
        api.captureThenNavigate(link.href, {
          target: link.getAttribute("target") || "_self",
        });
      });
    });

    scope.querySelectorAll("form[data-stripe-radar-form]").forEach(function (form) {
      if (form.dataset.stripeRadarBound === "true") {
        return;
      }
      form.dataset.stripeRadarBound = "true";
      form.addEventListener("submit", function (event) {
        if (event.defaultPrevented) {
          return;
        }
        event.preventDefault();
        api.captureThenSubmit(form);
      });
    });
  };

  window.GobiiStripeRadar = api;
  api.bind(document);
  waitForRadarSession();
})();
