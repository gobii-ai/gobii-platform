const STATE_KEY_PREFIX = "gobii:mcp_oauth_state:";
const SERVER_KEY_PREFIX = "gobii:mcp_oauth_server:";

function base64UrlEncode(arrayBuffer) {
  const bytes = Array.from(new Uint8Array(arrayBuffer));
  const binary = bytes.map((b) => String.fromCharCode(b)).join("");
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function getCsrfToken() {
  const match = document.cookie.match(/csrftoken=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : "";
}

async function sha256(input) {
  const encoder = new TextEncoder();
  const data = encoder.encode(input);
  const digest = await crypto.subtle.digest("SHA-256", data);
  return base64UrlEncode(digest);
}

function generateCodeVerifier() {
  const array = new Uint8Array(64);
  crypto.getRandomValues(array);
  return base64UrlEncode(array);
}

async function generatePkcePair() {
  const verifier = generateCodeVerifier();
  const challenge = await sha256(verifier);
  return { verifier, challenge };
}

function randomState() {
  const array = new Uint8Array(16);
  crypto.getRandomValues(array);
  return base64UrlEncode(array);
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Request failed with ${response.status}`);
  }
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    return response.json();
  }
  return response.text();
}

async function postJson(url, payload) {
  return fetchJson(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-CSRFToken": getCsrfToken(),
    },
    body: JSON.stringify(payload),
  });
}

function buildAuthorizationUrl(base, params) {
  const url = new URL(base);
  Object.entries(params).forEach(([key, value]) => {
    if (value) {
      url.searchParams.set(key, value);
    }
  });
  return url.toString();
}

function storePendingState(state, data) {
  localStorage.setItem(`${STATE_KEY_PREFIX}${state}`, JSON.stringify(data));
}

function clearPendingState(state) {
  localStorage.removeItem(`${STATE_KEY_PREFIX}${state}`);
}

function storeServerPending(serverId, data) {
  if (serverId) {
    localStorage.setItem(`${SERVER_KEY_PREFIX}${serverId}`, JSON.stringify(data));
  }
}

function clearServerPending(serverId) {
  if (serverId) {
    localStorage.removeItem(`${SERVER_KEY_PREFIX}${serverId}`);
  }
}

function readServerPending(serverId) {
  if (!serverId) {
    return null;
  }
  const raw = localStorage.getItem(`${SERVER_KEY_PREFIX}${serverId}`);
  if (!raw) {
    return null;
  }
  try {
    return JSON.parse(raw);
  } catch (error) {
    console.warn("Invalid pending OAuth payload", error);
    return null;
  }
}

function readPendingByState(state) {
  const raw = localStorage.getItem(`${STATE_KEY_PREFIX}${state}`);
  if (!raw) {
    return null;
  }
  try {
    return JSON.parse(raw);
  } catch (error) {
    console.warn("Invalid OAuth state payload", error);
    return null;
  }
}

function createStore(dataset) {
  const startUrl = dataset.oauthStartUrl;
  const metadataUrl = dataset.oauthMetadataUrl;
  const callbackPath = dataset.oauthCallbackPath || "/console/mcp/oauth/callback/";
  const statusUrl = dataset.oauthStatusUrl;
  const revokeUrl = dataset.oauthRevokeUrl;
  const serverId = dataset.serverId || "";
  const urlFieldId = dataset.oauthUrlFieldId || "";

  const callbackUrl = new URL(callbackPath, window.location.origin).toString();

  return {
    hasServer: Boolean(serverId),
    status: serverId ? "loading" : "idle",
    connecting: false,
    revoking: false,
    error: null,
    scope: null,
    expires_at: null,
    pendingState: null,
    getters: null,
    mount(getters) {
      this.getters = getters;
      this.refreshPending();
      if (this.hasServer && statusUrl) {
        this.refreshStatus();
      }
    },
    setAuthMethod(method) {
      this.enabled = method === "oauth2";
    },
    refreshPending() {
      if (!this.hasServer) {
        this.pendingState = null;
        return;
      }
      const pending = readServerPending(serverId);
      this.pendingState = pending ? pending.state : null;
      if (this.pendingState) {
        this.status = "pending";
      }
    },
    async refreshStatus() {
      if (!statusUrl) {
        return;
      }
      try {
        const payload = await fetchJson(statusUrl);
        if (payload.connected) {
          this.status = "connected";
          this.scope = payload.scope || null;
          this.expires_at = payload.expires_at || null;
        } else {
          this.status = this.pendingState ? "pending" : "disconnected";
          this.scope = null;
          this.expires_at = null;
        }
      } catch (error) {
        console.warn("Failed to load OAuth status", error);
        this.status = "disconnected";
      }
    },
    getServerUrl() {
      if (!urlFieldId) {
        return "";
      }
      const input = document.getElementById(urlFieldId);
      return input ? input.value.trim() : "";
    },
    async fetchMetadata(serverUrl) {
    return postJson(metadataUrl, {
      server_config_id: serverId,
      resource: "/.well-known/oauth-authorization-server",
    });
    },
    validatePrerequisites() {
      if (!this.enabled) {
        this.error = "Select OAuth 2.0 to enable this flow.";
        return false;
      }
      if (!this.hasServer) {
        this.error = "Save this MCP server first, then return to connect.";
        return false;
      }
      const serverUrl = this.getServerUrl();
      if (!serverUrl) {
        this.error = "Enter the MCP server URL before connecting.";
        return false;
      }
      if (!this.getters) {
        this.error = "Form not ready.";
        return false;
      }
      if (!this.getters.getClientId()) {
        this.error = "Provide an OAuth client ID.";
        return false;
      }
      this.error = null;
      return true;
    },
    async start() {
      if (!this.validatePrerequisites()) {
        return;
      }
      try {
        this.connecting = true;
        const serverUrl = this.getServerUrl();
        const metadata = await this.fetchMetadata(serverUrl);
        const authorizationEndpoint = metadata.authorization_endpoint;
        const tokenEndpoint = metadata.token_endpoint;
        if (!authorizationEndpoint || !tokenEndpoint) {
          throw new Error("OAuth metadata is missing authorization or token endpoints.");
        }
        const pkce = await generatePkcePair();
        const state = randomState();
        const body = {
          server_config_id: serverId,
          scope: this.getters.getScope(),
          token_endpoint: tokenEndpoint,
          code_challenge: pkce.challenge,
          code_challenge_method: "S256",
          code_verifier: pkce.verifier,
          redirect_uri: callbackUrl,
          client_id: this.getters.getClientId(),
          client_secret: this.getters.getClientSecret(),
          state,
        };
        const session = await postJson(startUrl, body);
        const redirectUrl = buildAuthorizationUrl(authorizationEndpoint, {
          response_type: "code",
          client_id: this.getters.getClientId(),
          redirect_uri: callbackUrl,
          state: session.state,
          scope: this.getters.getScope(),
          code_challenge: pkce.challenge,
          code_challenge_method: "S256",
        });
        storePendingState(session.state, {
          sessionId: session.session_id,
          serverId,
          returnUrl: window.location.href,
        });
        storeServerPending(serverId, {
          state: session.state,
          created_at: Date.now(),
        });
        this.status = "pending";
        window.location.href = redirectUrl;
      } catch (error) {
        console.error("OAuth start failed", error);
        this.error = error.message || "Failed to start OAuth flow.";
      } finally {
        this.connecting = false;
      }
    },
    async revoke() {
      if (!revokeUrl) {
        return;
      }
      try {
        this.revoking = true;
        await postJson(revokeUrl, {});
        this.status = "disconnected";
        this.scope = null;
        this.expires_at = null;
        this.error = null;
        clearServerPending(serverId);
      } catch (error) {
        console.error("Failed to revoke OAuth credentials", error);
        this.error = error.message || "Failed to revoke credentials.";
      } finally {
        this.revoking = false;
      }
    },
  };
}

window.createMCPOAuthStore = function (dataset) {
  return createStore(dataset);
};

window.getMcpPendingState = readPendingByState;
window.clearMcpPendingState = clearPendingState;
window.clearMcpServerPending = clearServerPending;
