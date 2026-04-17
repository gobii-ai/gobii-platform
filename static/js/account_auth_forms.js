(function () {
  const POPUP_STATE_PREFIX = "gobii:cta_auth_popup_state:";
  const POPUP_COMPLETE_KEY = "gobii:cta_auth_popup_complete";
  const FPJS_TIMEOUT_MS = 3000;
  let activeLoginForm = null;

  function getAuthRoots(root) {
    if (!root) {
      return [];
    }
    const roots = [];
    if (root instanceof Element && root.matches("[data-account-auth-root]")) {
      roots.push(root);
    }
    if (typeof root.querySelectorAll === "function") {
      root.querySelectorAll("[data-account-auth-root]").forEach((node) => roots.push(node));
    }
    return roots;
  }

  function getRootConfig(authRoot) {
    return {
      mode: authRoot.dataset.authMode || "page",
      nextUrl: authRoot.dataset.authNext || "",
      gaMeasurementId: authRoot.dataset.gaMeasurementId || "",
      fpjsEnabled: authRoot.dataset.fpjsEnabled === "true",
      fpjsLoaderUrl: authRoot.dataset.fpjsLoaderUrl || "",
      fpjsBehaviorUrl: authRoot.dataset.fpjsBehaviorUrl || "",
      popupCompleteUrl: authRoot.dataset.popupCompleteUrl || "",
    };
  }

  function shouldStageIdentitySignals(config) {
    return Boolean(config.fpjsEnabled && config.fpjsLoaderUrl);
  }

  function getSubmitButtons(form) {
    return Array.from(
      form.querySelectorAll('button[type="submit"], input[type="submit"]')
    );
  }

  function setButtonsDisabled(buttons, disabled) {
    buttons.forEach((button) => {
      button.disabled = disabled;
    });
  }

  function setFieldValue(authRoot, selector, value) {
    const field = authRoot.querySelector(selector);
    if (field) {
      field.value = value || "";
    }
  }

  function getClientSignalsController(authRoot) {
    if (authRoot._gobiiClientSignalsController) {
      return authRoot._gobiiClientSignalsController;
    }

    const config = getRootConfig(authRoot);
    const identitySignals = window.GobiiIdentitySignals;
    if (!shouldStageIdentitySignals(config) || !identitySignals) {
      authRoot._gobiiClientSignalsController = {
        readyPromise: Promise.resolve(),
      };
      return authRoot._gobiiClientSignalsController;
    }

    const cookieMaxAge = 60 * 60 * 2;

    identitySignals.clearStagedFpjsCookies();

    const gaClientIdPromise = identitySignals.createGaClientIdPromise({
      measurementId: config.gaMeasurementId,
      onResolved: (normalizedGaClientId) => {
        setFieldValue(authRoot, "[data-auth-ga-client-field]", normalizedGaClientId);
        identitySignals.writeCookie("gobii_signup_ga_client_id", normalizedGaClientId, cookieMaxAge);
      },
    });

    const fpjsPromise = identitySignals.createFpjsPromise({
      loaderUrl: config.fpjsLoaderUrl,
      behaviorUrl: config.fpjsBehaviorUrl,
      timeoutMs: FPJS_TIMEOUT_MS,
      onResolved: (result) => {
        if (!result) {
          return;
        }
        setFieldValue(authRoot, "[data-auth-fpjs-visitor-field]", result.visitorId);
        setFieldValue(authRoot, "[data-auth-fpjs-request-field]", result.requestId);
        identitySignals.writeCookie("gobii_signup_fpjs_visitor_id", result.visitorId, cookieMaxAge);
        identitySignals.writeCookie("gobii_signup_fpjs_request_id", result.requestId, cookieMaxAge);
      },
    });

    authRoot._gobiiClientSignalsController = {
      readyPromise: Promise.allSettled([fpjsPromise, gaClientIdPromise]),
    };
    return authRoot._gobiiClientSignalsController;
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

  function updateQueryParam(url, key, value) {
    const parsed = new URL(url, window.location.origin);
    if (!value) {
      parsed.searchParams.delete(key);
    } else {
      parsed.searchParams.set(key, value);
    }
    return `${parsed.pathname}${parsed.search}${parsed.hash}`;
  }

  function storePopupSession(state, targetUrl) {
    try {
      window.localStorage.setItem(
        `${POPUP_STATE_PREFIX}${state}`,
        JSON.stringify({
          targetUrl: sanitizeTargetUrl(targetUrl),
          createdAt: new Date().toISOString(),
        })
      );
    } catch (_error) {
      return false;
    }
    return true;
  }

  function readPopupSession(state) {
    if (!state) {
      return null;
    }
    try {
      const raw = window.localStorage.getItem(`${POPUP_STATE_PREFIX}${state}`);
      if (!raw) {
        return null;
      }
      return JSON.parse(raw);
    } catch (_error) {
      return null;
    }
  }

  function clearPopupSession(state) {
    if (!state) {
      return;
    }
    try {
      window.localStorage.removeItem(`${POPUP_STATE_PREFIX}${state}`);
    } catch (_error) {
      // Ignore storage cleanup failures.
    }
  }

  function openPopup(url) {
    const popup = window.open(
      url,
      "gobii-auth-popup",
      "popup=yes,width=560,height=740,resizable=yes,scrollbars=yes"
    );
    if (popup && typeof popup.focus === "function") {
      popup.focus();
    }
    return popup;
  }

  function prepareSocialHref(authRoot, link) {
    const config = getRootConfig(authRoot);
    if (link.dataset.authSocialPopup !== "true") {
      return {
        href: link.href,
        usePopup: false,
      };
    }

    const popupCompleteUrl = link.dataset.authPopupCompleteUrl || config.popupCompleteUrl;
    const targetUrl = sanitizeTargetUrl(config.nextUrl || "/");
    const popupState = (window.crypto && typeof window.crypto.randomUUID === "function")
      ? window.crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(36).slice(2)}`;

    if (!storePopupSession(popupState, targetUrl)) {
      return {
        href: updateQueryParam(link.href, "next", targetUrl),
        usePopup: false,
      };
    }

    return {
      href: updateQueryParam(
        link.href,
        "next",
        updateQueryParam(popupCompleteUrl, "auth_popup_state", popupState)
      ),
      usePopup: true,
    };
  }

  function handleSocialLinkClick(authRoot, link) {
    const controller = getClientSignalsController(authRoot);
    const prepared = prepareSocialHref(authRoot, link);
    controller.readyPromise.finally(() => {
      if (prepared.usePopup) {
        const popup = openPopup(prepared.href);
        if (popup) {
          delete link.dataset.authPending;
          return;
        }
      }
      window.location.href = prepared.href;
    });
  }

  function setTurnstileMessage(form, message) {
    const messageNode = form.querySelector("[data-turnstile-status]");
    if (!messageNode) {
      return;
    }
    messageNode.textContent = message;
    messageNode.classList.toggle("hidden", !message);
  }

  function setTurnstileSubmitEnabled(form, enabled) {
    const button = form.querySelector("[data-turnstile-submit]");
    if (!button) {
      return;
    }
    button.disabled = !enabled;
    button.classList.toggle("opacity-60", !enabled);
    button.classList.toggle("cursor-not-allowed", !enabled);
    button.setAttribute("aria-disabled", enabled ? "false" : "true");
  }

  function hasTurnstileToken(form) {
    const tokenField = form.querySelector('[name="cf-turnstile-response"]');
    return Boolean(tokenField && tokenField.value && tokenField.value.trim());
  }

  function resetTurnstile(form) {
    const widget = form.querySelector(".cf-turnstile");
    if (!widget || !window.turnstile || typeof window.turnstile.reset !== "function") {
      return;
    }
    window.turnstile.reset(widget);
  }

  function buildTurnstileOptions(widget) {
    const options = {};
    if (widget.dataset.sitekey) {
      options.sitekey = widget.dataset.sitekey;
    }
    if (widget.dataset.theme) {
      options.theme = widget.dataset.theme;
    }
    if (widget.dataset.size) {
      options.size = widget.dataset.size;
    }
    if (widget.dataset.action) {
      options.action = widget.dataset.action;
    }
    if (widget.dataset.cdata) {
      options.cData = widget.dataset.cdata;
    }
    if (widget.dataset.tabindex) {
      options.tabindex = Number(widget.dataset.tabindex);
    }
    if (widget.dataset.callback && typeof window[widget.dataset.callback] === "function") {
      options.callback = window[widget.dataset.callback];
    }
    if (widget.dataset.expiredCallback && typeof window[widget.dataset.expiredCallback] === "function") {
      options["expired-callback"] = window[widget.dataset.expiredCallback];
    }
    if (widget.dataset.timeoutCallback && typeof window[widget.dataset.timeoutCallback] === "function") {
      options["timeout-callback"] = window[widget.dataset.timeoutCallback];
    }
    if (widget.dataset.errorCallback && typeof window[widget.dataset.errorCallback] === "function") {
      options["error-callback"] = window[widget.dataset.errorCallback];
    }
    return options;
  }

  function initTurnstiles(root, attempt) {
    const authRoot = root instanceof Element ? root : null;
    if (!authRoot) {
      return;
    }
    const widgets = authRoot.querySelectorAll(".cf-turnstile");
    if (!widgets.length) {
      return;
    }
    if (!window.turnstile || typeof window.turnstile.render !== "function") {
      if ((attempt || 0) >= 10) {
        return;
      }
      window.setTimeout(() => initTurnstiles(authRoot, (attempt || 0) + 1), 200);
      return;
    }
    widgets.forEach((widget) => {
      if (widget.dataset.gobiiTurnstileRendered === "true") {
        return;
      }
      window.turnstile.render(widget, buildTurnstileOptions(widget));
      widget.dataset.gobiiTurnstileRendered = "true";
    });
  }

  function replaceModalContent(html) {
    if (window.GobiiCtaSignupModal && typeof window.GobiiCtaSignupModal.replaceContent === "function") {
      window.GobiiCtaSignupModal.replaceContent(html);
    }
  }

  function showModalError(message) {
    if (window.GobiiCtaSignupModal && typeof window.GobiiCtaSignupModal.showError === "function") {
      window.GobiiCtaSignupModal.showError(message);
    }
  }

  async function submitModalForm(form) {
    const authRoot = form.closest("[data-account-auth-root]");
    const formData = new FormData(form);

    try {
      const response = await fetch(form.action, {
        method: "POST",
        body: formData,
        credentials: "same-origin",
        headers: {
          "X-Requested-With": "XMLHttpRequest",
          "Accept": "application/json",
        },
      });
      const payload = await response.json();
      if (payload.location) {
        window.location.assign(payload.location);
        return;
      }
      if (payload.auth_url) {
        if (window.GobiiCtaSignupModal && typeof window.GobiiCtaSignupModal.open === "function") {
          window.GobiiCtaSignupModal.open(payload.auth_url);
          return;
        }
        window.location.assign(payload.auth_url);
        return;
      }
      if (payload.html) {
        replaceModalContent(payload.html);
        return;
      }
      throw new Error("Unexpected authentication response.");
    } catch (error) {
      let fallbackMessage = "Unable to continue right now.";
      if (authRoot && authRoot.dataset.authTab === "signup") {
        fallbackMessage = "Unable to complete sign up right now.";
      } else if (authRoot && authRoot.dataset.authTab === "login") {
        fallbackMessage = "Unable to complete sign in right now.";
      }
      showModalError((error && error.message) || fallbackMessage);
    }
  }

  function initSignupForm(authRoot) {
    const form = authRoot.querySelector("[data-password-signup-form]");
    if (!form || form.dataset.authInitialized === "true") {
      return;
    }
    form.dataset.authInitialized = "true";

    const config = getRootConfig(authRoot);
    const submitButtons = getSubmitButtons(form);
    const controller = getClientSignalsController(authRoot);

    form.addEventListener("submit", (event) => {
      if (form.dataset.submitting === "true") {
        event.preventDefault();
        return;
      }

      if (config.mode === "page") {
        if (!shouldStageIdentitySignals(config) || form.dataset.clientSignalsReady === "true") {
          return;
        }
        event.preventDefault();
        if (form.dataset.clientSignalsPending === "true") {
          return;
        }
        form.dataset.clientSignalsPending = "true";
        setButtonsDisabled(submitButtons, true);
        controller.readyPromise.finally(() => {
          form.dataset.clientSignalsReady = "true";
          form.dataset.submitting = "true";
          form.submit();
        });
        return;
      }

      event.preventDefault();
      if (form.dataset.clientSignalsPending === "true") {
        return;
      }
      form.dataset.clientSignalsPending = "true";
      setButtonsDisabled(submitButtons, true);
      controller.readyPromise.finally(() => {
        form.dataset.submitting = "true";
        submitModalForm(form);
      });
    });
  }

  function initEmailStartForm(authRoot) {
    const form = authRoot.querySelector("[data-auth-email-start-form]");
    if (!form || form.dataset.authInitialized === "true") {
      return;
    }
    form.dataset.authInitialized = "true";
    const submitButtons = getSubmitButtons(form);

    form.addEventListener("submit", (event) => {
      event.preventDefault();
      if (form.dataset.submitting === "true") {
        return;
      }
      form.dataset.submitting = "true";
      setButtonsDisabled(submitButtons, true);
      submitModalForm(form).finally(() => {
        form.dataset.submitting = "false";
        setButtonsDisabled(submitButtons, false);
      });
    });
  }

  function initLoginForm(authRoot) {
    const form = authRoot.querySelector("[data-login-form]");
    if (!form || form.dataset.authInitialized === "true") {
      return;
    }
    form.dataset.authInitialized = "true";
    activeLoginForm = form;

    const config = getRootConfig(authRoot);
    form._gobiiTurnstileState = {
      submitPending: false,
    };

    if (!hasTurnstileToken(form) && form.querySelector(".cf-turnstile")) {
      setTurnstileSubmitEnabled(form, false);
    }

    form.addEventListener("submit", (event) => {
      if (form.dataset.submitting === "true") {
        event.preventDefault();
        return;
      }

      if (form.querySelector(".cf-turnstile") && !hasTurnstileToken(form)) {
        event.preventDefault();
        form._gobiiTurnstileState.submitPending = true;
        setTurnstileSubmitEnabled(form, false);
        setTurnstileMessage(form, "Completing verification...");
        return;
      }

      if (config.mode !== "modal") {
        return;
      }

      event.preventDefault();
      form.dataset.submitting = "true";
      submitModalForm(form);
    });
  }

  function finalizeLoginSubmit(form) {
    const config = getRootConfig(form.closest("[data-account-auth-root]"));
    if (config.mode === "modal") {
      form.dataset.submitting = "true";
      submitModalForm(form);
      return;
    }
    form.dataset.submitting = "true";
    form.requestSubmit(form.querySelector("[data-turnstile-submit]") || undefined);
  }

  function bindSocialLinks(authRoot) {
    authRoot
      .querySelectorAll("[data-social-auth-link], [data-social-signup-link]")
      .forEach((link) => {
        if (link.dataset.authInitialized === "true") {
          return;
        }
        link.dataset.authInitialized = "true";
        link.addEventListener("click", (event) => {
          if (link.dataset.authPending === "true") {
            event.preventDefault();
            return;
          }
          event.preventDefault();
          link.dataset.authPending = "true";
          handleSocialLinkClick(authRoot, link);
        });
      });
  }

  function bindModalNavLinks(authRoot) {
    authRoot.querySelectorAll("[data-auth-modal-link]").forEach((link) => {
      if (link.dataset.authModalNavInitialized === "true") {
        return;
      }
      link.dataset.authModalNavInitialized = "true";
      link.addEventListener("click", (event) => {
        const modalUrl = link.dataset.authModalUrl || link.getAttribute("href");
        if (!modalUrl) {
          return;
        }
        event.preventDefault();
        event.stopPropagation();
        if (window.GobiiCtaSignupModal && typeof window.GobiiCtaSignupModal.open === "function") {
          window.GobiiCtaSignupModal.open(modalUrl);
          return;
        }
        window.location.assign(modalUrl);
      });
    });
  }

  function init(root) {
    getAuthRoots(root).forEach((authRoot) => {
      initTurnstiles(authRoot, 0);
      initEmailStartForm(authRoot);
      initSignupForm(authRoot);
      initLoginForm(authRoot);
      bindSocialLinks(authRoot);
      bindModalNavLinks(authRoot);
    });
  }

  window.gobiiLoginTurnstileSuccess = function () {
    if (!activeLoginForm) {
      return;
    }
    setTurnstileSubmitEnabled(activeLoginForm, true);
    setTurnstileMessage(activeLoginForm, "");
    if (!activeLoginForm._gobiiTurnstileState.submitPending) {
      return;
    }
    activeLoginForm._gobiiTurnstileState.submitPending = false;
    finalizeLoginSubmit(activeLoginForm);
  };

  window.gobiiLoginTurnstileExpired = function () {
    if (!activeLoginForm) {
      return;
    }
    activeLoginForm._gobiiTurnstileState.submitPending = false;
    setTurnstileSubmitEnabled(activeLoginForm, false);
    setTurnstileMessage(activeLoginForm, "Verification expired. Please try again.");
    resetTurnstile(activeLoginForm);
  };

  window.gobiiLoginTurnstileError = function () {
    if (!activeLoginForm) {
      return;
    }
    activeLoginForm._gobiiTurnstileState.submitPending = false;
    setTurnstileSubmitEnabled(activeLoginForm, false);
    setTurnstileMessage(activeLoginForm, "Verification failed. Please try again.");
    resetTurnstile(activeLoginForm);
  };

  window.addEventListener("storage", (event) => {
    if (event.key !== POPUP_COMPLETE_KEY || !event.newValue) {
      return;
    }
    try {
      const payload = JSON.parse(event.newValue);
      const session = readPopupSession(payload.state);
      if (!session || !session.targetUrl) {
        return;
      }
      clearPopupSession(payload.state);
      if (window.GobiiCtaSignupModal && typeof window.GobiiCtaSignupModal.close === "function") {
        window.GobiiCtaSignupModal.close();
      }
      window.location.assign(session.targetUrl);
    } catch (_error) {
      // Ignore malformed storage payloads.
    }
  });

  window.GobiiAccountAuthForms = {
    init,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => init(document));
  } else {
    init(document);
  }
})();
