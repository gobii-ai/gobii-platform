(function () {
  const loaderScript = document.currentScript
    || document.getElementById("gobii-deferred-auth-modal-loader");
  if (!loaderScript) {
    return;
  }

  const assetSources = [
    loaderScript.dataset.identitySrc,
    loaderScript.dataset.authFormsSrc,
    loaderScript.dataset.modalSrc,
  ].filter(Boolean);
  const turnstileSource = loaderScript.dataset.turnstileSrc || "";
  const USER_ACTION_LOAD_TIMEOUT_MS = 8000;
  const loadedSources = new Set();
  const loadingSources = new Map();
  let assetsPromise = null;
  let pendingAction = null;
  let listenersActive = true;

  function absoluteUrl(source) {
    return new URL(source, window.location.href).href;
  }

  function loadScript(source, options) {
    if (!source) {
      return Promise.resolve();
    }

    const resolvedSource = absoluteUrl(source);
    if (loadedSources.has(resolvedSource)) {
      return Promise.resolve();
    }
    if (loadingSources.has(resolvedSource)) {
      return loadingSources.get(resolvedSource);
    }

    const sourcePromise = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = resolvedSource;
      script.async = Boolean(options && options.async);
      script.dataset.gobiiDeferredAuthAsset = "true";
      script.addEventListener("load", () => {
        loadedSources.add(resolvedSource);
        loadingSources.delete(resolvedSource);
        resolve();
      }, { once: true });
      script.addEventListener("error", () => {
        loadingSources.delete(resolvedSource);
        script.remove();
        reject(new Error(`Unable to load deferred authentication asset: ${resolvedSource}`));
      }, { once: true });
      document.head.appendChild(script);
    });
    loadingSources.set(resolvedSource, sourcePromise);
    return sourcePromise;
  }

  function loadInOrder(sources) {
    return sources.reduce(
      (promise, source) => promise.then(() => loadScript(source)),
      Promise.resolve()
    );
  }

  function ensureAssetsLoaded() {
    const turnstileReady = !turnstileSource
      || loadedSources.has(absoluteUrl(turnstileSource));
    if (window.GobiiCtaSignupModal && turnstileReady) {
      removeIntentListeners();
      return Promise.resolve();
    }
    if (assetsPromise) {
      return assetsPromise;
    }

    const turnstilePromise = loadScript(turnstileSource, { async: true });
    assetsPromise = Promise.all([
      loadInOrder(assetSources),
      turnstilePromise,
    ])
      .then(() => {
        if (!window.GobiiCtaSignupModal) {
          throw new Error("Deferred authentication modal did not initialize.");
        }
        removeIntentListeners();
      })
      .catch((error) => {
        assetsPromise = null;
        throw error;
      });
    return assetsPromise;
  }

  function isSpawnForm(form) {
    if (!(form instanceof HTMLFormElement) || form.method.toLowerCase() !== "post") {
      return false;
    }
    const action = form.getAttribute("action") || "";
    return action === "/spawn-agent/" || action.endsWith("/hire/");
  }

  function getModalLink(target) {
    if (!(target instanceof Element)) {
      return null;
    }
    const modalLink = target.closest("[data-auth-modal-link]");
    if (modalLink) {
      return modalLink;
    }
    const pricingLink = target.closest(".plan-cta");
    if (!pricingLink || pricingLink.closest("[data-account-auth-root]")) {
      return null;
    }
    const pricingPage = pricingLink.closest("#pricing-page");
    return pricingPage && pricingPage.dataset.currentPlanPaid !== "true"
      ? pricingLink
      : null;
  }

  function getModalForm(target) {
    if (!(target instanceof Element)) {
      return null;
    }
    const form = target.closest("form");
    return isSpawnForm(form) ? form : null;
  }

  function showLoadingModal() {
    const modal = document.getElementById("cta-signup-modal");
    if (!modal) {
      return;
    }
    const body = modal.querySelector("[data-cta-signup-modal-body]");
    const errorBox = modal.querySelector("[data-cta-signup-modal-error]");
    const loading = modal.querySelector("[data-cta-signup-modal-loading]");
    if (body) {
      body.innerHTML = "";
    }
    if (errorBox) {
      errorBox.textContent = "";
      errorBox.classList.add("hidden");
    }
    if (loading) {
      loading.classList.remove("hidden");
    }
    window.dispatchEvent(new CustomEvent("open-modal", {
      detail: { id: "cta-signup-modal" },
    }));
  }

  function hideLoadingState() {
    const modal = document.getElementById("cta-signup-modal");
    const loading = modal && modal.querySelector("[data-cta-signup-modal-loading]");
    if (loading) {
      loading.classList.add("hidden");
    }
  }

  function replayAction(action) {
    if (action.type === "submit") {
      if (typeof action.form.requestSubmit === "function") {
        action.form.requestSubmit(action.submitter || undefined);
      } else {
        HTMLFormElement.prototype.submit.call(action.form);
      }
      return;
    }
    action.link.click();
  }

  function fallbackAction(action) {
    if (action.type === "submit") {
      HTMLFormElement.prototype.submit.call(action.form);
      return;
    }
    const fallbackUrl = action.link.getAttribute("href");
    if (fallbackUrl) {
      window.location.assign(fallbackUrl);
    }
  }

  function ensureAssetsLoadedForAction() {
    let timeoutId = null;
    const timeoutPromise = new Promise((_resolve, reject) => {
      timeoutId = window.setTimeout(() => {
        reject(new Error("Deferred authentication assets timed out."));
      }, USER_ACTION_LOAD_TIMEOUT_MS);
    });

    return Promise.race([ensureAssetsLoaded(), timeoutPromise])
      .finally(() => {
        if (timeoutId !== null) {
          window.clearTimeout(timeoutId);
        }
      });
  }

  function runPendingAction(action) {
    if (pendingAction) {
      return;
    }
    pendingAction = action;
    showLoadingModal();
    ensureAssetsLoadedForAction()
      .then(() => {
        const completedAction = pendingAction;
        pendingAction = null;
        if (!completedAction || completedAction.cancelled) {
          hideLoadingState();
          return;
        }
        replayAction(completedAction);
      })
      .catch(() => {
        const failedAction = pendingAction;
        pendingAction = null;
        hideLoadingState();
        if (failedAction && !failedAction.cancelled) {
          fallbackAction(failedAction);
        }
      });
  }

  function onClick(event) {
    const link = getModalLink(event.target);
    if (!link) {
      return;
    }
    event.preventDefault();
    event.stopImmediatePropagation();
    runPendingAction({ type: "click", link });
  }

  function onSubmit(event) {
    if (!isSpawnForm(event.target)) {
      return;
    }
    event.preventDefault();
    event.stopImmediatePropagation();
    runPendingAction({
      type: "submit",
      form: event.target,
      submitter: event.submitter || null,
    });
  }

  function warmAssets(event) {
    if (getModalLink(event.target) || getModalForm(event.target)) {
      ensureAssetsLoaded().catch(() => {});
    }
  }

  function onModalDismissed(event) {
    if (
      pendingAction
      && event.detail
      && event.detail.id === "cta-signup-modal"
    ) {
      pendingAction.cancelled = true;
    }
  }

  function removeIntentListeners() {
    if (!listenersActive) {
      return;
    }
    listenersActive = false;
    document.removeEventListener("click", onClick, true);
    document.removeEventListener("submit", onSubmit, true);
    document.removeEventListener("pointerover", warmAssets, true);
    document.removeEventListener("pointerdown", warmAssets, true);
    document.removeEventListener("focusin", warmAssets, true);
    window.removeEventListener("gobii-modal-dismissed", onModalDismissed);
  }

  document.addEventListener("click", onClick, true);
  document.addEventListener("submit", onSubmit, true);
  document.addEventListener("pointerover", warmAssets, true);
  document.addEventListener("pointerdown", warmAssets, true);
  document.addEventListener("focusin", warmAssets, true);
  window.addEventListener("gobii-modal-dismissed", onModalDismissed);

  function scheduleIdleLoad() {
    const loadWhenIdle = () => {
      if ("requestIdleCallback" in window) {
        window.requestIdleCallback(() => {
          ensureAssetsLoaded().catch(() => {});
        }, { timeout: 2000 });
        return;
      }
      window.setTimeout(() => {
        ensureAssetsLoaded().catch(() => {});
      }, 250);
    };

    if (document.readyState === "complete") {
      loadWhenIdle();
    } else {
      window.addEventListener("load", loadWhenIdle, { once: true });
    }
  }

  window.GobiiDeferredAuthModalAssets = {
    load: ensureAssetsLoaded,
  };
  scheduleIdleLoad();
})();
