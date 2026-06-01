const statusEl = document.getElementById("native-oauth-status");
const errorEl = document.getElementById("native-oauth-error");
const COMPLETE_MESSAGE_TYPE = "gobii:native-oauth-complete";
const COMPLETE_STORAGE_PREFIX = "gobii:native_oauth_complete:";

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
  setStatus("Unable to complete the connection.");
}

function getCsrfToken() {
  const meta = document.querySelector('meta[name="csrf-cookie-name"]');
  const cookieName = (meta && meta.getAttribute("content") && meta.getAttribute("content").trim()) || "csrftoken";
  const match = document.cookie.match(new RegExp(`${cookieName}=([^;]+)`));
  return match ? decodeURIComponent(match[1]) : "";
}

function getPendingSession(state) {
  const raw = localStorage.getItem(`gobii:native_oauth_state:${state}`);
  if (!raw) {
    return null;
  }
  try {
    return JSON.parse(raw);
  } catch (error) {
    console.warn("Invalid native integration OAuth session payload", error);
    return null;
  }
}

function buildCallbackHeaders(sessionData) {
  const headers = {
    "Content-Type": "application/json",
    "X-CSRFToken": getCsrfToken(),
  };
  const context = sessionData && sessionData.context;
  if (context && typeof context === "object" && context.type && context.id) {
    headers["X-Gobii-Context-Type"] = String(context.type);
    headers["X-Gobii-Context-Id"] = String(context.id);
  }
  return headers;
}

function hasOpener() {
  try {
    return Boolean(window.opener && !window.opener.closed);
  } catch (error) {
    return false;
  }
}

function notifyOpener(payload) {
  const message = {
    type: COMPLETE_MESSAGE_TYPE,
    ...payload,
  };
  try {
    localStorage.setItem(`${COMPLETE_STORAGE_PREFIX}${Date.now()}`, JSON.stringify(message));
  } catch (error) {
    console.warn("Failed to persist native integration OAuth completion", error);
  }

  if (!hasOpener()) {
    return false;
  }
  try {
    window.opener.postMessage(message, window.location.origin);
    return true;
  } catch (error) {
    return false;
  }
}

function resultUrl(rawUrl, result) {
  const url = new URL(rawUrl || "/app/integrations", window.location.origin);
  url.searchParams.set("native_oauth", result);
  return url.toString();
}

function redirectBack(sessionData, result) {
  const returnUrl = sessionData && sessionData.returnUrl;
  window.location.href = resultUrl(returnUrl, result);
}

function finishWithError(message, sessionData) {
  const isPopupFlow = Boolean(sessionData && sessionData.popup);
  const notified = sessionData
    && notifyOpener({
      ok: false,
      providerKey: sessionData.providerKey,
      error: message,
    });
  showError(message);
  if (notified || isPopupFlow) {
    setTimeout(() => {
      window.close();
    }, 900);
  }
}

async function completeOAuth() {
  const params = new URLSearchParams(window.location.search);
  const error = params.get("error");
  const code = params.get("code");
  const state = params.get("state");
  const sessionData = state ? getPendingSession(state) : null;

  if (error) {
    if (state) {
      localStorage.removeItem(`gobii:native_oauth_state:${state}`);
    }
    finishWithError(`Provider returned an error: ${error}`, sessionData);
    return;
  }

  if (!code || !state) {
    finishWithError("Missing authorization code or state parameter.", sessionData);
    return;
  }

  if (!sessionData || !sessionData.providerKey) {
    finishWithError("OAuth session expired. Please start the flow again.", sessionData);
    return;
  }

  setStatus("Securely storing integration...");

  try {
    const response = await fetch(`/console/api/native-integrations/${encodeURIComponent(sessionData.providerKey)}/callback/`, {
      method: "POST",
      headers: buildCallbackHeaders(sessionData),
      body: JSON.stringify({
        authorization_code: code,
        state,
      }),
    });

    if (!response.ok) {
      const detail = await response.text();
      throw new Error(detail || "Callback failed");
    }

    localStorage.removeItem(`gobii:native_oauth_state:${state}`);
    const isPopupFlow = Boolean(sessionData.popup);
    const notified = notifyOpener({ ok: true, providerKey: sessionData.providerKey });
    if (notified || isPopupFlow) {
      setStatus("Connection complete. You can close this tab.");
      setTimeout(() => {
        window.close();
      }, 700);
      return;
    }

    setStatus("Connection complete. Redirecting...");
    setTimeout(() => redirectBack(sessionData, "success"), 900);
  } catch (err) {
    console.error("Native integration OAuth callback failed", err);
    const message = err.message || "Failed to store integration tokens.";
    finishWithError(message, sessionData);
    if (!hasOpener() && !(sessionData && sessionData.popup)) {
      setTimeout(() => redirectBack(sessionData, "error"), 1800);
    }
  }
}

completeOAuth();
