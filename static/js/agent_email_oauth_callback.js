const statusEl = document.getElementById("email-oauth-status");
const errorEl = document.getElementById("email-oauth-error");

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
  setStatus("Unable to complete OAuth flow.");
}

function getCsrfToken() {
  const match = document.cookie.match(/csrftoken=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : "";
}

function getPendingSession(state) {
  const raw = localStorage.getItem(`gobii:email_oauth_state:${state}`);
  if (!raw) {
    return null;
  }
  try {
    return JSON.parse(raw);
  } catch (error) {
    console.warn("Invalid OAuth session payload", error);
    return null;
  }
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
  if (!sessionData) {
    showError("OAuth session expired. Please start the flow again.");
    return;
  }

  setStatus("Securely storing tokens...");

  try {
    const response = await fetch("/console/api/email/oauth/callback/", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": getCsrfToken(),
      },
      body: JSON.stringify({
        session_id: sessionData.sessionId,
        authorization_code: code,
        state,
      }),
    });

    if (!response.ok) {
      const detail = await response.text();
      throw new Error(detail || "Callback failed");
    }

    localStorage.removeItem(`gobii:email_oauth_state:${state}`);
    setStatus("Connection complete! Redirecting...");
    const payload = sessionData.returnUrl || "/console/agents/";
    setTimeout(() => {
      window.location.href = payload;
    }, 1200);
  } catch (err) {
    console.error("OAuth callback failed", err);
    showError(err.message || "Failed to store OAuth tokens.");
  }
}

completeOAuth();
