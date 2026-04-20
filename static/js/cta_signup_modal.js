(function () {
  const MODAL_ID = "cta-signup-modal";
  let loadSequence = 0;

  function readConfig() {
    const script = document.getElementById("gobii-cta-signup-modal-config");
    if (!script) {
      return null;
    }
    try {
      return JSON.parse(script.textContent);
    } catch (_error) {
      return null;
    }
  }

  function compactProperties(props) {
    const compacted = {};
    Object.keys(props || {}).forEach((key) => {
      const value = props[key];
      if (value === undefined || value === null || value === "") {
        return;
      }
      compacted[key] = value;
    });
    return compacted;
  }

  function derivePageSlug(pathname) {
    if (!pathname || pathname === "/") {
      return "home";
    }

    return pathname
      .replace(/^\/+|\/+$/g, "")
      .replace(/[/-]+/g, "_")
      .replace(/[^a-zA-Z0-9_]+/g, "_")
      .replace(/_+/g, "_")
      .toLowerCase();
  }

  function track(eventName, properties) {
    if (!eventName || !window.analytics || typeof window.analytics.track !== "function") {
      return;
    }

    window.analytics.track(eventName, compactProperties(properties));
  }

  function generateSessionId() {
    if (window.crypto && typeof window.crypto.randomUUID === "function") {
      return window.crypto.randomUUID();
    }
    return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  }

  function normalizeStep(rawStep) {
    const step = (rawStep || "").trim().toLowerCase();
    if (step === "email-start") {
      return "email_start";
    }
    if (step === "login" || step === "signup") {
      return step;
    }
    return "";
  }

  function sanitizeTargetUrl(rawUrl) {
    if (!rawUrl) {
      return "/";
    }
    try {
      const parsed = new URL(rawUrl, window.location.origin);
      if (parsed.origin !== window.location.origin) {
        return "/";
      }
      return `${parsed.pathname}${parsed.search}${parsed.hash}` || "/";
    } catch (_error) {
      return "/";
    }
  }

  function getPathname(rawUrl) {
    if (!rawUrl) {
      return "";
    }
    try {
      return new URL(rawUrl, window.location.origin).pathname;
    } catch (_error) {
      return "";
    }
  }

  function init() {
    const config = readConfig();
    if (!config || !config.enabled) {
      return;
    }

    const modal = document.getElementById(MODAL_ID);
    if (!modal) {
      return;
    }

    const body = modal.querySelector("[data-cta-signup-modal-body]");
    const loading = modal.querySelector("[data-cta-signup-modal-loading]");
    const errorBox = modal.querySelector("[data-cta-signup-modal-error]");
    const signupPath = getPathname(config.signup_url);
    const loginPath = getPathname(config.login_url);
    const modalState = {
      isOpen: false,
      sessionId: "",
      originContext: null,
      currentStep: "",
      requestedStep: "",
    };

    function setLoading(isLoading) {
      loading.classList.toggle("hidden", !isLoading);
    }

    function showError(message) {
      if (!message) {
        errorBox.classList.add("hidden");
        errorBox.textContent = "";
        return;
      }
      errorBox.textContent = message;
      errorBox.classList.remove("hidden");
    }

    function openModal() {
      window.dispatchEvent(new CustomEvent("open-modal", {
        detail: { id: MODAL_ID },
      }));
    }

    function buildPageContext(pagePath, pageSlug) {
      const resolvedPath = pagePath || window.location.pathname || "/";
      return {
        pagePath: resolvedPath,
        pageSlug: pageSlug || derivePageSlug(resolvedPath),
      };
    }

    function getNextPathFromUrl(rawUrl) {
      if (!rawUrl) {
        return "";
      }
      try {
        const parsed = new URL(rawUrl, window.location.origin);
        const nextValue = parsed.searchParams.get("next") || "";
        if (!nextValue) {
          return "";
        }
        return sanitizeTargetUrl(nextValue);
      } catch (_error) {
        return "";
      }
    }

    function getRequestedStepFromUrl(rawUrl) {
      if (!rawUrl) {
        return "";
      }
      try {
        const parsed = new URL(rawUrl, window.location.origin);
        if (parsed.pathname === loginPath) {
          return "login";
        }
        if (parsed.pathname !== signupPath) {
          return "";
        }
        return parsed.searchParams.get("step") === "password" ? "signup" : "email_start";
      } catch (_error) {
        return "";
      }
    }

    function getRouteFromUrl(rawUrl) {
      const requestedStep = getRequestedStepFromUrl(rawUrl);
      if (requestedStep === "login") {
        return "login";
      }
      if (requestedStep === "signup" || requestedStep === "email_start") {
        return "signup";
      }
      return "";
    }

    function buildOriginContext(triggerElement, options) {
      const trackingProps = window.gobiiGetCtaTrackingProperties
        ? window.gobiiGetCtaTrackingProperties(triggerElement, {
            submitter: options && options.submitter ? options.submitter : null,
          })
        : {};
      const pageContext = buildPageContext(
        trackingProps.page_path,
        trackingProps.page_slug
      );

      return {
        origin_cta_id: trackingProps.cta_id || "",
        origin_placement: trackingProps.placement || "",
        origin_intent: trackingProps.intent || "",
        origin_source_page: trackingProps.source_page || "",
        origin_page_path: pageContext.pagePath,
        origin_page_slug: pageContext.pageSlug,
        next_path: getNextPathFromUrl(options && options.url ? options.url : ""),
      };
    }

    function getCommonProps(extraProperties) {
      const origin = modalState.originContext || {};
      const pageContext = buildPageContext(
        origin.origin_page_path,
        origin.origin_page_slug
      );

      return compactProperties(Object.assign({
        medium: "Web",
        page_path: pageContext.pagePath,
        page_slug: pageContext.pageSlug,
        modal_session_id: modalState.sessionId || "",
        step: modalState.currentStep || modalState.requestedStep || "",
        origin_cta_id: origin.origin_cta_id || "",
        origin_placement: origin.origin_placement || "",
        origin_intent: origin.origin_intent || "",
        origin_source_page: origin.origin_source_page || "",
        origin_page_path: pageContext.pagePath,
        origin_page_slug: pageContext.pageSlug,
        next_path: origin.next_path || "",
      }, extraProperties || {}));
    }

    function getAnalyticsContext(stepOverride) {
      const step = normalizeStep(stepOverride) || modalState.currentStep || modalState.requestedStep || "";
      const commonProps = getCommonProps({ step: step });
      return {
        page_path: commonProps.page_path,
        page_slug: commonProps.page_slug,
        source_page: commonProps.origin_source_page || commonProps.page_slug,
        properties: {
          modal_session_id: commonProps.modal_session_id,
          modal_step: step,
          origin_cta_id: commonProps.origin_cta_id,
          origin_placement: commonProps.origin_placement,
          origin_intent: commonProps.origin_intent,
          origin_source_page: commonProps.origin_source_page,
          origin_page_path: commonProps.origin_page_path,
          origin_page_slug: commonProps.origin_page_slug,
          next_path: commonProps.next_path,
        },
      };
    }

    function resetModalState() {
      loadSequence += 1;
      showError("");
      setLoading(false);
      body.innerHTML = "";
      modalState.isOpen = false;
      modalState.sessionId = "";
      modalState.originContext = null;
      modalState.currentStep = "";
      modalState.requestedStep = "";
    }

    function beginSession(triggerElement, options) {
      modalState.isOpen = true;
      modalState.sessionId = generateSessionId();
      modalState.originContext = buildOriginContext(triggerElement, options);
      modalState.currentStep = "";
      modalState.requestedStep = getRequestedStepFromUrl(options && options.url ? options.url : "");
      track(config.events && config.events.opened, getCommonProps());
    }

    function finalizeClose(reason) {
      if (!modalState.sessionId) {
        resetModalState();
        return;
      }

      track(config.events && config.events.closed, getCommonProps({
        close_reason: reason || "button",
      }));
      resetModalState();
    }

    function closeModal(reason, options) {
      if (!modalState.sessionId) {
        resetModalState();
        return;
      }

      window.dispatchEvent(new CustomEvent("close-modal", {
        detail: { id: MODAL_ID },
      }));
      if (options && options.track === false) {
        resetModalState();
        return;
      }
      finalizeClose(reason || "button");
    }

    function trackStepViewed(step) {
      const normalizedStep = normalizeStep(step);
      if (!normalizedStep) {
        return;
      }

      modalState.currentStep = normalizedStep;
      track(config.events && config.events.step_viewed, getCommonProps({
        step: normalizedStep,
      }));
    }

    function trackFailure(payload) {
      const failureKind = payload && payload.failureKind ? payload.failureKind : "unexpected_response";
      const step = normalizeStep(payload && payload.step ? payload.step : "")
        || modalState.currentStep
        || modalState.requestedStep
        || "";
      track(config.events && config.events.failed, getCommonProps({
        step: step,
        failure_kind: failureKind,
      }));
    }

    function trackEmailRouted(route) {
      if (!route) {
        return;
      }

      track(config.events && config.events.email_routed, getCommonProps({
        step: "email_start",
        route: route,
      }));
    }

    async function loadAuthFragment(url, options) {
      const requestId = ++loadSequence;
      if (!modalState.isOpen || !modalState.sessionId) {
        beginSession(options && options.triggerElement ? options.triggerElement : null, {
          url: url,
          submitter: options && options.submitter ? options.submitter : null,
        });
        openModal();
      } else {
        modalState.requestedStep = getRequestedStepFromUrl(url) || modalState.requestedStep;
      }

      showError("");
      setLoading(true);
      body.innerHTML = "";

      try {
        const response = await fetch(url, {
          credentials: "same-origin",
        });
        if (!response.ok) {
          throw new Error("Unable to load authentication options.");
        }
        const html = await response.text();
        if (requestId !== loadSequence || !modalState.sessionId) {
          return;
        }
        replaceContent(html);
      } catch (error) {
        if (requestId !== loadSequence || !modalState.sessionId) {
          return;
        }
        body.innerHTML = "";
        setLoading(false);
        showError((error && error.message) || "Unable to load authentication options.");
        trackFailure({
          step: getRequestedStepFromUrl(url),
          failureKind: "network",
        });
      }
    }

    function replaceContent(html) {
      setLoading(false);
      showError("");
      body.innerHTML = html;
      if (window.GobiiAccountAuthForms && typeof window.GobiiAccountAuthForms.init === "function") {
        window.GobiiAccountAuthForms.init(body);
      }

      const authRoot = body.querySelector("[data-account-auth-root]");
      trackStepViewed(authRoot ? authRoot.dataset.authTab : "");
    }

    function buildModalSignupUrl(nextUrl) {
      const parsed = new URL(config.signup_url, window.location.origin);
      parsed.searchParams.set("next", sanitizeTargetUrl(nextUrl));
      return `${parsed.pathname}${parsed.search}${parsed.hash}`;
    }

    function isSpawnForm(form) {
      if (!form || form.method.toLowerCase() !== "post") {
        return false;
      }
      const action = form.getAttribute("action") || "";
      return (
        action === "/spawn-agent/" ||
        action.indexOf("/pretrained-workers/") !== -1 ||
        action.endsWith("/hire/")
      );
    }

    async function prepareSpawnAuth(form) {
      const formData = new FormData(form);
      formData.append("auth_modal", "1");

      const response = await fetch(form.action, {
        method: "POST",
        body: formData,
        credentials: "same-origin",
        headers: {
          "Accept": "application/json",
        },
      });
      if (!response.ok) {
        throw new Error("Unable to prepare sign up.");
      }
      return response.json();
    }

    document.addEventListener("click", (event) => {
      const target = event.target instanceof Element ? event.target : null;
      if (!target) {
        return;
      }

      const closeButton = target.closest("[data-cta-signup-modal-close]");
      if (closeButton) {
        event.preventDefault();
        closeModal("button");
        return;
      }

      const modalLink = target.closest("[data-auth-modal-link]");
      if (modalLink) {
        const modalUrl = modalLink.dataset.authModalUrl || modalLink.getAttribute("href");
        if (!modalUrl) {
          return;
        }
        event.preventDefault();
        loadAuthFragment(modalUrl, {
          triggerElement: modalLink,
        });
        return;
      }

      const pricingLink = target.closest(".plan-cta");
      if (pricingLink && !pricingLink.closest("[data-account-auth-root]")) {
        const pricingPage = pricingLink.closest("#pricing-page");
        if (!pricingPage || pricingPage.dataset.currentPlanPaid === "true") {
          return;
        }
        event.preventDefault();
        loadAuthFragment(buildModalSignupUrl(pricingLink.href), {
          triggerElement: pricingLink,
        });
      }
    });

    document.addEventListener("submit", (event) => {
      if (!(event.target instanceof HTMLFormElement)) {
        return;
      }
      const form = event.target;
      if (!isSpawnForm(form)) {
        return;
      }
      event.preventDefault();
      prepareSpawnAuth(form)
        .then((payload) => {
          if (!payload || !payload.auth_url) {
            throw new Error("Missing authentication URL.");
          }
          return loadAuthFragment(payload.auth_url, {
            triggerElement: form,
            submitter: event.submitter || null,
          });
        })
        .catch(() => {
          form.submit();
        });
    });

    window.addEventListener("gobii-modal-dismissed", (event) => {
      const detail = event.detail || {};
      if (detail.id !== MODAL_ID) {
        return;
      }
      finalizeClose(detail.reason || "backdrop");
    });

    window.GobiiCtaSignupModal = {
      close: closeModal,
      open: loadAuthFragment,
      replaceContent,
      showError,
      trackFailure,
      trackEmailRouted,
      getRouteFromUrl,
      getCtaTrackingOptions: getAnalyticsContext,
    };
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
