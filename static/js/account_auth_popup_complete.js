const statusEl = document.getElementById("auth-popup-complete-status");
const errorEl = document.getElementById("auth-popup-complete-error");
const POPUP_STATE_PREFIX = "gobii:cta_auth_popup_state:";
const POPUP_COMPLETE_KEY = "gobii:cta_auth_popup_complete";

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

(function completeAuthPopup() {
  const params = new URLSearchParams(window.location.search);
  const popupState = params.get("auth_popup_state");
  const sessionData = readPopupSession(popupState);

  if (!popupState || !sessionData) {
    showError("Authentication session expired. Please start again.");
    return;
  }

  notifyCompletion(popupState);
  setStatus("Authentication complete. Returning to Gobii...");

  if (hasOpener()) {
    window.setTimeout(() => {
      window.close();
    }, 600);
    return;
  }

  if (sessionData.targetUrl) {
    window.setTimeout(() => {
      window.location.assign(sessionData.targetUrl);
    }, 600);
  }
})();
