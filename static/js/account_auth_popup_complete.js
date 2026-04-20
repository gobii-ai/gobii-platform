const statusEl = document.getElementById("auth-popup-complete-status");
const errorEl = document.getElementById("auth-popup-complete-error");
const POPUP_STATE_PREFIX = "gobii:cta_auth_popup_state:";
const POPUP_COMPLETE_KEY = "gobii:cta_auth_popup_complete";
const SIGNUP_TRACKING_TIMEOUT_MS = 1500;

function setStatus(text) {
  if (statusEl) {
    statusEl.textContent = text;
  }
}

function showError(text) {
  if (errorEl) {
    errorEl.textContent = text;
    errorEl.classList.remove("hidden");
  }
  setStatus("Unable to complete authentication.");
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

function notifyCompletion(state) {
  window.localStorage.setItem(
    POPUP_COMPLETE_KEY,
    JSON.stringify({
      state,
      completedAt: new Date().toISOString(),
    })
  );
}

function hasOpener() {
  try {
    return Boolean(window.opener && !window.opener.closed);
  } catch (_error) {
    return false;
  }
}

function focusOpener() {
  if (!hasOpener()) {
    return;
  }
  try {
    window.opener.focus();
  } catch (_error) {
    // Ignore focus failures.
  }
}

function navigateOpener(targetUrl) {
  if (!targetUrl || !hasOpener()) {
    return;
  }
  try {
    window.opener.location.assign(targetUrl);
  } catch (_error) {
    // Ignore cross-window navigation failures; storage-event fallback still applies.
  }
}

function attemptCloseWindow() {
  try {
    window.close();
  } catch (_error) {
    // Ignore close failures and try a second strategy below.
  }

  window.setTimeout(() => {
    try {
      window.open("", "_self");
      window.close();
    } catch (_error) {
      // Ignore fallback close failures.
    }
  }, 100);
}

function waitForSignupTracking() {
  if (!window.GobiiSignupTracking || typeof window.GobiiSignupTracking.fetchAndFire !== "function") {
    return Promise.resolve(false);
  }

  return Promise.race([
    window.GobiiSignupTracking.fetchAndFire({
      endpoint: "/clear_signup_tracking",
      source: "auth_popup_complete",
      maxRetries: 2,
      baseDelayMs: 400,
    }),
    new Promise((resolve) => {
      window.setTimeout(() => resolve(false), SIGNUP_TRACKING_TIMEOUT_MS);
    }),
  ]);
}

(async function completeAuthPopup() {
  const params = new URLSearchParams(window.location.search);
  const popupState = params.get("auth_popup_state");
  const sessionData = readPopupSession(popupState);

  if (!popupState || !sessionData) {
    showError("Authentication session expired. Please start again.");
    return;
  }

  setStatus("Completing sign in...");
  await waitForSignupTracking();
  notifyCompletion(popupState);
  setStatus("Authentication complete. Returning to Gobii...");
  focusOpener();
  navigateOpener(sessionData.targetUrl);
  attemptCloseWindow();

  window.setTimeout(() => {
    if (hasOpener()) {
      setStatus("Authentication complete. You can close this tab.");
      return;
    }

    if (sessionData.targetUrl) {
      window.location.replace(sessionData.targetUrl);
    }
  }, 900);
})();
