const statusEl = document.getElementById("native-oauth-status");
const errorEl = document.getElementById("native-oauth-error");

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
  const match = document.cookie.match(/csrftoken=([^;]+)/);
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

async function completeOAuth() {
  const params = new URLSearchParams(window.location.search);
  const error = params.get("error");
  const code = params.get("code");
  const state = params.get("state");

  if (error) {
    showError(`Provider returned an error: ${error}`);
    return;
  }

  if (!code || !state) {
    showError("Missing authorization code or state parameter.");
    return;
  }

  const sessionData = getPendingSession(state);
  if (!sessionData || !sessionData.providerKey) {
    showError("OAuth session expired. Please start the flow again.");
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
    setStatus("Connection complete. Redirecting...");
    const returnUrl = sessionData.returnUrl || "/app/integrations?native_oauth=success";
    setTimeout(() => {
      window.location.href = returnUrl;
    }, 900);
  } catch (err) {
    console.error("Native integration OAuth callback failed", err);
    showError(err.message || "Failed to store integration tokens.");
  }
}

completeOAuth();
