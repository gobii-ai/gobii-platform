(function () {
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

  function init() {
    const config = readConfig();
    if (!config || !config.enabled) {
      return;
    }

    const modal = document.getElementById("cta-signup-modal");
    if (!modal) {
      return;
    }

    const body = modal.querySelector("[data-cta-signup-modal-body]");
    const loading = modal.querySelector("[data-cta-signup-modal-loading]");
    const errorBox = modal.querySelector("[data-cta-signup-modal-error]");

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
        detail: { id: "cta-signup-modal" },
      }));
    }

    function closeModal() {
      window.dispatchEvent(new CustomEvent("close-modal", {
        detail: { id: "cta-signup-modal" },
      }));
      showError("");
      setLoading(false);
      body.innerHTML = "";
    }

    async function loadAuthFragment(url) {
      openModal();
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
        replaceContent(html);
      } catch (error) {
        body.innerHTML = "";
        setLoading(false);
        showError((error && error.message) || "Unable to load authentication options.");
      }
    }

    function replaceContent(html) {
      setLoading(false);
      showError("");
      body.innerHTML = html;
      if (window.GobiiAccountAuthForms && typeof window.GobiiAccountAuthForms.init === "function") {
        window.GobiiAccountAuthForms.init(body);
      }
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
        closeModal();
        return;
      }

      const modalLink = target.closest("[data-auth-modal-link]");
      if (modalLink) {
        const modalUrl = modalLink.dataset.authModalUrl || modalLink.getAttribute("href");
        if (!modalUrl) {
          return;
        }
        event.preventDefault();
        loadAuthFragment(modalUrl);
        return;
      }

      const pricingLink = target.closest(".plan-cta");
      if (pricingLink && !pricingLink.closest("[data-account-auth-root]")) {
        const pricingPage = pricingLink.closest("#pricing-page");
        if (!pricingPage || pricingPage.dataset.currentPlanPaid === "true") {
          return;
        }
        event.preventDefault();
        loadAuthFragment(buildModalSignupUrl(pricingLink.href));
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
          return loadAuthFragment(payload.auth_url);
        })
        .catch(() => {
          form.submit();
        });
    });

    window.GobiiCtaSignupModal = {
      close: closeModal,
      open: loadAuthFragment,
      replaceContent,
      showError,
    };
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
