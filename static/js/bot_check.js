(function () {
  "use strict";

  let devtoolsAgentDetected = false;
  window.addEventListener("devtoolstooldiscovery", function () {
    devtoolsAgentDetected = true;
  });

  const STATUS_META = {
    clear: { label: "No signal", icon: "check" },
    flagged: { label: "Flagged", icon: "flag" },
    info: { label: "Informational", icon: "info" },
    unavailable: { label: "Unavailable", icon: "circle-help" },
  };

  // Slow identification responses can create a provider event before returning
  // its ID to the page, so both the agent and our wrapper need explicit headroom.
  const FINGERPRINT_GET_TIMEOUT_MS = 20000;
  const FINGERPRINT_WRAPPER_TIMEOUT_MS = 25000;
  let scanRunning = false;

  function setProgress(title, detail, currentStep) {
    const titleNode = document.getElementById("bot-check-progress-title");
    const detailNode = document.getElementById("bot-check-progress-detail");
    if (titleNode) titleNode.textContent = title;
    if (detailNode) detailNode.textContent = detail;
    document.querySelectorAll("[data-scan-step]").forEach(function (step) {
      const stepNumber = Number(step.dataset.scanStep);
      const state = stepNumber < currentStep ? "complete" : (
        stepNumber === currentStep ? "current" : "upcoming"
      );
      step.dataset.state = state;
      if (state === "current") {
        step.setAttribute("aria-current", "step");
      } else {
        step.removeAttribute("aria-current");
      }
      const marker = step.querySelector(".bot-check-step-marker");
      if (marker) marker.textContent = state === "complete" ? "✓" : String(stepNumber);
    });
  }

  function refreshIcons() {
    if (window.lucide && typeof window.lucide.createIcons === "function") {
      window.lucide.createIcons();
    }
  }

  function csrfToken() {
    if (typeof window.getCsrfTokenValue === "function") {
      return window.getCsrfTokenValue() || "";
    }
    return "";
  }

  async function postJson(url, payload) {
    const response = await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": csrfToken(),
      },
      body: JSON.stringify(payload || {}),
    });
    let data = {};
    try {
      data = await response.json();
    } catch (_error) {
      data = {};
    }
    return { response, data };
  }

  function storageAvailable(name) {
    try {
      const storage = window[name];
      const key = "__gobii_bot_check__";
      storage.setItem(key, "1");
      storage.removeItem(key);
      return true;
    } catch (_error) {
      return false;
    }
  }

  function webglInfo() {
    try {
      const canvas = document.createElement("canvas");
      const gl = canvas.getContext("webgl") || canvas.getContext("experimental-webgl");
      if (!gl) {
        return { vendor: "", renderer: "", software: false };
      }
      const extension = gl.getExtension("WEBGL_debug_renderer_info");
      const vendor = extension
        ? String(gl.getParameter(extension.UNMASKED_VENDOR_WEBGL) || "")
        : String(gl.getParameter(gl.VENDOR) || "");
      const renderer = extension
        ? String(gl.getParameter(extension.UNMASKED_RENDERER_WEBGL) || "")
        : String(gl.getParameter(gl.RENDERER) || "");
      const software = /(swiftshader|llvmpipe|software|mesa offscreen|microsoft basic render)/i.test(
        `${vendor} ${renderer}`
      );
      return { vendor, renderer, software };
    } catch (_error) {
      return { vendor: "", renderer: "", software: false };
    }
  }

  function automationGlobals() {
    const candidates = [
      "_phantom",
      "callPhantom",
      "__nightmare",
      "__selenium_unwrapped",
      "__webdriver_evaluate",
      "__driver_evaluate",
      "selenium",
      "domAutomation",
      "domAutomationController",
    ];
    return candidates.filter(function (name) {
      return Object.prototype.hasOwnProperty.call(window, name);
    });
  }

  function userAgentMismatch() {
    const data = navigator.userAgentData;
    if (!data) return false;
    const ua = navigator.userAgent || "";
    const platform = String(data.platform || "").toLowerCase();
    const expectations = [
      { pattern: /Windows/i, platform: "windows" },
      { pattern: /Android/i, platform: "android" },
      { pattern: /(iPhone|iPad|iPod)/i, platform: "ios" },
      { pattern: /Macintosh/i, platform: "macos" },
      { pattern: /CrOS/i, platform: "chrome os" },
    ];
    const expected = expectations.find(function (item) {
      return item.pattern.test(ua);
    });
    if (expected && platform && platform !== expected.platform) return true;
    const uaAppearsMobile = /(Mobi|Android|iPhone|iPad|iPod)/i.test(ua);
    return typeof data.mobile === "boolean" && data.mobile !== uaAppearsMobile;
  }

  function probeCdp() {
    return new Promise(function (resolve) {
      let detected = false;
      try {
        const error = new Error("bot-check-cdp-probe");
        Object.defineProperty(error, "stack", {
          configurable: true,
          get: function () {
            detected = true;
            return "";
          },
        });
        console.debug(error);
      } catch (_error) {
        resolve(false);
        return;
      }
      window.setTimeout(function () {
        resolve(detected);
      }, 75);
    });
  }

  async function collectClientSignals() {
    const graphics = webglInfo();
    const cdpDetected = await probeCdp();
    const ua = navigator.userAgent || "";
    return {
      webdriver: typeof navigator.webdriver === "boolean" ? navigator.webdriver : null,
      headless_user_agent: /(HeadlessChrome|PhantomJS|SlimerJS)/i.test(ua),
      automation_globals: automationGlobals(),
      devtools_agent: devtoolsAgentDetected,
      cdp_detected: cdpDetected,
      ua_ch_mismatch: userAgentMismatch(),
      languages: Array.isArray(navigator.languages) ? navigator.languages.slice(0, 10) : [],
      platform: navigator.userAgentData?.platform || navigator.platform || "",
      hardware_concurrency:
        typeof navigator.hardwareConcurrency === "number" ? navigator.hardwareConcurrency : null,
      device_memory: typeof navigator.deviceMemory === "number" ? navigator.deviceMemory : null,
      max_touch_points:
        typeof navigator.maxTouchPoints === "number" ? navigator.maxTouchPoints : null,
      screen_width: typeof screen.width === "number" ? screen.width : null,
      screen_height: typeof screen.height === "number" ? screen.height : null,
      color_depth: typeof screen.colorDepth === "number" ? screen.colorDepth : null,
      cookies_enabled:
        typeof navigator.cookieEnabled === "boolean" ? navigator.cookieEnabled : null,
      local_storage: storageAvailable("localStorage"),
      session_storage: storageAvailable("sessionStorage"),
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "",
      webgl_vendor: graphics.vendor,
      webgl_renderer: graphics.renderer,
      software_renderer: graphics.software,
    };
  }

  function fingerprintPromise(config) {
    if (!config || !config.enabled) {
      return Promise.resolve(null);
    }
    const identitySignals = window.GobiiIdentitySignals;
    if (!identitySignals?.createFpjsPromise) {
      return Promise.resolve({
        result: null,
        failure: "agent_error",
      });
    }
    let failure = "";
    const onError = function (code) {
      failure = code;
    };
    const request = identitySignals.createFpjsPromise({
      loaderUrl: config.loader_url,
      behaviorUrl: config.behavior_url || "",
      timeoutMs: FINGERPRINT_WRAPPER_TIMEOUT_MS,
      getOptions: { timeout: FINGERPRINT_GET_TIMEOUT_MS },
      onError,
    });

    return request.then(function (result) {
      return {
        result,
        failure: result ? "" : failure || "timeout",
      };
    });
  }

  function wait(milliseconds) {
    return new Promise(function (resolve) {
      window.setTimeout(resolve, milliseconds);
    });
  }

  function createTextElement(tagName, className, text) {
    const node = document.createElement(tagName);
    if (className) node.className = className;
    node.textContent = text;
    return node;
  }

  function renderCheck(check) {
    const row = document.createElement("div");
    row.className = "bot-check-row";

    const main = document.createElement("div");
    main.className = "bot-check-row-main";

    const iconWrap = document.createElement("span");
    iconWrap.className = "bot-check-status-icon";
    iconWrap.dataset.status = check.status;
    const icon = document.createElement("i");
    icon.dataset.lucide = STATUS_META[check.status]?.icon || "circle-help";
    icon.className = "h-4 w-4";
    icon.setAttribute("aria-hidden", "true");
    iconWrap.appendChild(icon);

    const copy = document.createElement("div");
    copy.className = "min-w-0";
    copy.appendChild(createTextElement("h3", "font-semibold text-slate-950", check.label));
    copy.appendChild(createTextElement("p", "mt-1 break-words text-sm font-medium text-slate-700", check.value));
    copy.appendChild(createTextElement("p", "mt-1 text-sm leading-6 text-slate-600", check.detail));
    const sourceText = check.contribution
      ? `${check.source} · +${check.contribution} score`
      : check.source;
    copy.appendChild(
      createTextElement("p", "mt-2 text-xs font-semibold uppercase tracking-wide text-slate-500", sourceText)
    );

    main.appendChild(iconWrap);
    main.appendChild(copy);

    const pill = createTextElement(
      "span",
      "bot-check-pill",
      STATUS_META[check.status]?.label || "Unavailable"
    );
    pill.dataset.status = check.status;

    row.appendChild(main);
    row.appendChild(pill);
    return row;
  }

  function renderCategory(category) {
    const section = document.createElement("details");
    section.className = "bot-check-category";
    const flaggedCount = category.checks.filter(function (check) {
      return check.status === "flagged";
    }).length;
    section.open = flaggedCount > 0 || category.key === "automation";

    const header = document.createElement("summary");
    header.className = "bot-check-category-summary";
    const copy = document.createElement("div");
    copy.className = "bot-check-category-summary-copy";
    const heading = createTextElement(
      "h2",
      "text-xl font-semibold tracking-tight text-slate-950",
      category.label
    );
    copy.appendChild(heading);
    copy.appendChild(
      createTextElement("p", "mt-2 text-sm leading-6 text-slate-600", category.description)
    );
    header.appendChild(copy);

    const meta = document.createElement("span");
    meta.className = "bot-check-category-summary-meta";
    const countLabel = flaggedCount
      ? `${flaggedCount} flagged`
      : `${category.checks.length} checks`;
    const count = createTextElement("span", "bot-check-category-count", countLabel);
    count.dataset.hasFlags = flaggedCount ? "true" : "false";
    const chevron = document.createElement("i");
    chevron.dataset.lucide = "chevron-down";
    chevron.className = "bot-check-category-chevron h-5 w-5";
    chevron.setAttribute("aria-hidden", "true");
    meta.appendChild(count);
    meta.appendChild(chevron);
    header.appendChild(meta);

    const checks = document.createElement("div");
    checks.className = "bot-check-checks";
    const priority = { flagged: 0, clear: 1, info: 2, unavailable: 3 };
    category.checks.slice().sort(function (first, second) {
      return priority[first.status] - priority[second.status];
    }).forEach(function (check) {
      checks.appendChild(renderCheck(check));
    });

    section.appendChild(header);
    section.appendChild(checks);
    return section;
  }

  function fingerprintStatusLabel(status) {
    const labels = {
      complete: "Fingerprint intelligence: complete",
      browser_only: "FingerprintJS: connected · Smart Signals need a server API key",
      unavailable: "Fingerprint intelligence: unavailable",
      missing_event: "Fingerprint agent returned no event · reload the page and try again",
      timed_out: "Fingerprint intelligence: timed out; partial report shown",
      client_error: "Fingerprint agent: blocked, timed out, or failed to initialize",
      client_csp_block: "Fingerprint agent blocked by the page security policy",
      client_forbidden_origin: "Fingerprint rejected this website origin",
      client_invalid_browser_key: "Fingerprint browser token is invalid or unavailable",
      client_network_error: "Fingerprint agent could not reach the provider",
      client_timeout: "Fingerprint agent timed out before returning an event",
      error: "Fingerprint intelligence: unavailable after provider error",
    };
    return labels[status] || `Fingerprint intelligence: ${status || "unavailable"}`;
  }

  function renderReport(report) {
    const progress = document.getElementById("bot-check-progress");
    const results = document.getElementById("bot-check-results");
    const verdict = document.getElementById("bot-check-verdict");
    const categories = document.getElementById("bot-check-categories");

    progress.classList.add("hidden");
    results.classList.remove("hidden");
    document.getElementById("bot-check-root").setAttribute("aria-busy", "false");
    verdict.dataset.tone = report.verdict.tone;
    document.getElementById("bot-check-verdict-label").textContent = report.verdict.label;
    document.getElementById("bot-check-verdict-summary").textContent = report.verdict.summary;
    document.getElementById("bot-check-score-value").textContent = String(report.verdict.score);
    document.getElementById(
      "bot-check-coverage"
    ).textContent = `${report.coverage.completed} of ${report.coverage.total} checks returned data`;
    document.getElementById("bot-check-fingerprint-status").textContent = fingerprintStatusLabel(
      report.fingerprint_status
    );

    categories.replaceChildren();
    report.categories.forEach(function (category) {
      categories.appendChild(renderCategory(category));
    });

    const json = JSON.stringify(report, null, 2);
    document.getElementById("bot-check-json").textContent = json;
    document.getElementById("bot-check-copy").dataset.report = json;
    scanRunning = false;
    setActionDisabled(false);
    refreshIcons();
  }

  function setActionDisabled(disabled) {
    ["bot-check-retry", "bot-check-rerun"].forEach(function (id) {
      const button = document.getElementById(id);
      if (button) button.disabled = disabled;
    });
  }

  function showError(error) {
    const message = error?.message || "An unexpected error prevented the diagnostic from completing.";
    const code = error?.code || "unknown";
    document.getElementById("bot-check-progress").classList.add("hidden");
    document.getElementById("bot-check-results").classList.add("hidden");
    document.getElementById("bot-check-error").classList.remove("hidden");
    document.getElementById("bot-check-root").setAttribute("aria-busy", "false");
    document.getElementById("bot-check-error-message").textContent = message;
    const guidance = document.getElementById("bot-check-error-guidance");
    const retry = document.getElementById("bot-check-retry");
    if (code === "rate_limited") {
      guidance.textContent = "Wait before reloading this page. Repeated attempts will not start a new scan.";
      retry.classList.add("hidden");
    } else {
      guidance.textContent = "You can retry safely; no partial result was saved.";
      retry.classList.remove("hidden");
    }
    scanRunning = false;
    setActionDisabled(false);
    refreshIcons();
  }

  async function runScan() {
    const root = document.getElementById("bot-check-root");
    if (!root || scanRunning) return;
    scanRunning = true;
    setActionDisabled(true);
    root.setAttribute("aria-busy", "true");
    document.getElementById("bot-check-error").classList.add("hidden");
    document.getElementById("bot-check-results").classList.add("hidden");
    document.getElementById("bot-check-progress").classList.remove("hidden");

    setProgress("Reading browser environment…", "Collecting bounded runtime and device observations.", 1);
    const clientPromise = collectClientSignals();
    const startResult = await postJson(root.dataset.startUrl, {});
    if (!startResult.response.ok) {
      const error = new Error(startResult.data.error || "The scan could not be admitted.");
      error.code = startResult.data.code || "request_failed";
      throw error;
    }

    setProgress("Checking network context…", "Comparing browser evidence with the server-observed request.", 2);
    const clientSignals = await clientPromise;

    if (startResult.data.fingerprint?.enabled) {
      if (startResult.data.fingerprint.server_intelligence_enabled) {
        setProgress("Checking device intelligence…", "Retrieving server-verified Fingerprint signals.", 3);
      } else {
        setProgress("Checking browser identity…", "Running FingerprintJS with the configured browser token.", 3);
      }
    } else {
      setProgress("Building your report…", "Fingerprint is unavailable; completing the local diagnostic.", 3);
    }
    const fpOutcome = await fingerprintPromise(startResult.data.fingerprint);
    const fpResult = fpOutcome?.result || null;

    const completePayload = {
      scan_token: startResult.data.scan_token,
      client_signals: clientSignals,
      fingerprint_event_id:
        fpResult?.event_id || fpResult?.eventId || fpResult?.requestId || "",
      fingerprint_client: {
        visitor_found:
          typeof fpResult?.visitor_found === "boolean"
            ? fpResult.visitor_found
            : typeof fpResult?.visitorFound === "boolean"
              ? fpResult.visitorFound
              : null,
        confidence:
          typeof fpResult?.confidence?.score === "number" ? fpResult.confidence.score : null,
        integration_error: fpOutcome?.failure || "",
      },
    };

    while (true) {
      const completeResult = await postJson(root.dataset.completeUrl, completePayload);
      if (completeResult.response.status === 202) {
        setProgress(
          "Fingerprint is still processing…",
          "The provider event is propagating. The local report is ready.",
          3
        );
        await wait(completeResult.data.retry_after_ms || 2000);
        continue;
      }
      if (!completeResult.response.ok) {
        const error = new Error(
          completeResult.data.error || "The diagnostic could not be completed."
        );
        error.code = completeResult.data.code || "request_failed";
        throw error;
      }
      renderReport(completeResult.data.report);
      return;
    }
  }

  function setupActions() {
    document.getElementById("bot-check-retry")?.addEventListener("click", function () {
      runScan().catch(function (error) {
        showError(error);
      });
    });
    document.getElementById("bot-check-rerun")?.addEventListener("click", function () {
      window.location.reload();
    });

    document.getElementById("bot-check-copy")?.addEventListener("click", async function (event) {
      const button = event.currentTarget;
      const label = document.getElementById("bot-check-copy-label");
      try {
        await navigator.clipboard.writeText(button.dataset.report || "");
        label.textContent = "Copied";
      } catch (_error) {
        const report = document.getElementById("bot-check-json");
        const details = report.closest("details");
        if (details) details.open = true;
        const selection = window.getSelection();
        const range = document.createRange();
        range.selectNodeContents(report);
        selection.removeAllRanges();
        selection.addRange(range);
        label.textContent = "JSON selected";
      }
      window.setTimeout(function () {
        label.textContent = "Copy JSON";
      }, 1800);
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    setupActions();
    runScan().catch(function (error) {
      showError(error);
    });
  });
})();
