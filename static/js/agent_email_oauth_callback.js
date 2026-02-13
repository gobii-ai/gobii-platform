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
  if (typeof window.getCsrfTokenValue === "function") {
    return window.getCsrfTokenValue() || "";
  }

  const meta = document.querySelector('meta[name="csrf-cookie-name"]');
  const cookieName = (meta && meta.getAttribute("content") && meta.getAttribute("content").trim()) || "csrftoken";
  const cookies = document.cookie ? document.cookie.split(";") : [];
  for (let i = 0; i < cookies.length; i += 1) {
    const parts = cookies[i].trim().split("=");
    if (parts[0] === cookieName) {
      return decodeURIComponent(parts.slice(1).join("="));
    }
  }
  return "";
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

function notifyCompletion(sessionData) {
  const accountId = sessionData.accountId;
  const completionKey = accountId ? `gobii:email_oauth_complete:${accountId}` : "gobii:email_oauth_complete";
  const payload = {
    accountId,
    completedAt: new Date().toISOString(),
  };
  localStorage.setItem(completionKey, JSON.stringify(payload));
}

function hasOpener() {
  try {
    return Boolean(window.opener && !window.opener.closed);
  } catch (error) {
    return false;
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
      credentials: "same-origin",
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
    notifyCompletion(sessionData);
    setStatus("Connection complete! You can close this tab.");
    if (hasOpener()) {
      setTimeout(() => {
        window.close();
      }, 800);
    }
  } catch (err) {
    console.error("OAuth callback failed", err);
    showError(err.message || "Failed to store OAuth tokens.");
  }
}

completeOAuth();
