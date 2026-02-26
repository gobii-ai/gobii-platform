const statusEl = document.getElementById("mcp-oauth-status");
const errorEl = document.getElementById("mcp-oauth-error");

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
  const raw = localStorage.getItem(`gobii:mcp_oauth_state:${state}`);
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
  const remoteAuth = params.get("remote_auth");
  const remoteAuthSessionId = params.get("remote_auth_session_id");
  const pendingSession = state ? getPendingSession(state) : null;
  const explicitRemoteAuth = remoteAuth === "1" || Boolean(remoteAuthSessionId);
  const inferredRemoteAuth = Boolean(state) && !pendingSession;
  const isRemoteAuth = explicitRemoteAuth || inferredRemoteAuth;
  const resolvedRemoteAuthSessionId = remoteAuthSessionId || state || "";

  if (isRemoteAuth) {
    if (!resolvedRemoteAuthSessionId) {
      showError("Missing remote auth session identifier.");
      return;
    }
    setStatus("Completing remote authorization...");
    try {
      const response = await fetch("/console/api/mcp/remote-auth/authorize/", {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": getCsrfToken(),
        },
        body: JSON.stringify({
          session_id: resolvedRemoteAuthSessionId,
          authorization_code: code || "",
          state: state || "",
          error: error || "",
        }),
      });

      if (!response.ok) {
        const detail = await response.text();
        throw new Error(detail || "Remote callback failed");
      }

      localStorage.setItem(
        `gobii:mcp_remote_auth_complete:${resolvedRemoteAuthSessionId}`,
        JSON.stringify({
          sessionId: resolvedRemoteAuthSessionId,
          completedAt: new Date().toISOString(),
        }),
      );
      setStatus("Remote authorization complete.");
      setTimeout(() => {
        if (window.opener && !window.opener.closed) {
          window.close();
          return;
        }
        window.location.href = "/console/advanced/mcp-servers/?remote_auth=success";
      }, 500);
    } catch (err) {
      console.error("Remote OAuth callback failed", err);
      showError(err.message || "Failed to complete remote authorization.");
    }
    return;
  }

  if (error) {
    showError(`Provider returned an error: ${error}`);
    return;
  }

  if (!code || !state) {
    showError("Missing authorization code or state parameter.");
    return;
  }

  if (!pendingSession) {
    showError("OAuth session expired. Please start the flow again.");
    return;
  }

  setStatus("Securely storing tokens…");

  try {
    const response = await fetch("/console/api/mcp/oauth/callback/", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": getCsrfToken(),
      },
      body: JSON.stringify({
        session_id: pendingSession.sessionId,
        authorization_code: code,
        state,
      }),
    });

    if (!response.ok) {
      const detail = await response.text();
      throw new Error(detail || "Callback failed");
    }

    clearPendingKeys(pendingSession.serverId, state);
    setStatus("Connection complete! Redirecting…");
    const payload = pendingSession.returnUrl || "/console/advanced/mcp-servers/?oauth=success";
    setTimeout(() => {
      window.location.href = payload;
    }, 1200);
  } catch (err) {
    console.error("OAuth callback failed", err);
    showError(err.message || "Failed to store OAuth tokens.");
  }
}

function clearPendingKeys(serverId, state) {
  localStorage.removeItem(`gobii:mcp_oauth_state:${state}`);
  if (serverId) {
    localStorage.removeItem(`gobii:mcp_oauth_server:${serverId}`);
  }
}

completeOAuth();
