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

function base64UrlEncode(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (let i = 0; i < bytes.length; i += 1) {
    binary += String.fromCharCode(bytes[i]);
  }
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function randomString(length) {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~";
  const randomValues = new Uint8Array(length);
  window.crypto.getRandomValues(randomValues);
  let result = "";
  randomValues.forEach((value) => {
    result += alphabet[value % alphabet.length];
  });
  return result;
}

async function sha256(input) {
  const encoder = new TextEncoder();
  const data = encoder.encode(input);
  return window.crypto.subtle.digest("SHA-256", data);
}

function showError(errorEl, message) {
  if (!errorEl) {
    return;
  }
  errorEl.textContent = message;
  errorEl.classList.remove("hidden");
}

function clearError(errorEl) {
  if (!errorEl) {
    return;
  }
  errorEl.textContent = "";
  errorEl.classList.add("hidden");
}

const panel = document.getElementById("email-oauth-panel");
if (panel && panel.dataset.accountId) {
  const providerSelect = document.getElementById("email-oauth-provider");
  const providerHidden = document.getElementById("email-oauth-provider-hidden");
  const tenantRow = document.getElementById("email-oauth-tenant-row");
  const tenantInput = document.getElementById("email-oauth-tenant");
  const authRow = document.getElementById("email-oauth-auth-row");
  const authInput = document.getElementById("email-oauth-auth-url");
  const tokenRow = document.getElementById("email-oauth-token-row");
  const tokenInput = document.getElementById("email-oauth-token-url");
  const clientIdRow = document.getElementById("email-oauth-client-id-row");
  const clientSecretRow = document.getElementById("email-oauth-client-secret-row");
  const clientIdInput = document.getElementById("email-oauth-client-id");
  const clientSecretInput = document.getElementById("email-oauth-client-secret");
  const connectionModeSelect = document.getElementById("email-connection-mode");
  const form = panel.closest("form");
  const endpointAddressInput = form ? form.querySelector('input[name="endpoint_address"]') : null;
  const customOnlyFields = document.querySelectorAll(".email-custom-only");
  const serverSettingsFields = document.querySelectorAll(".email-server-settings");
  const protocolSections = document.querySelectorAll(".email-protocol-section");
  const protocolActions = document.querySelectorAll(".email-protocol-action");
  const smtpAuthSelect = document.querySelector('select[name="smtp_auth"]');
  const imapAuthSelect = document.querySelector('select[name="imap_auth"]');
  const scopeInput = document.getElementById("email-oauth-scope");
  const connectButton = document.getElementById("email-oauth-connect");
  const disconnectButton = document.getElementById("email-oauth-disconnect");
  const statusEl = document.getElementById("email-oauth-status");
  const errorEl = document.getElementById("email-oauth-error");

  const accountId = panel.dataset.accountId;
  const callbackPath = panel.dataset.oauthCallbackPath || "";
  const startUrl = panel.dataset.oauthStartUrl || "";
  const statusUrl = panel.dataset.oauthStatusUrl || "";
  const revokeUrl = panel.dataset.oauthRevokeUrl || "";
  const returnUrl = panel.dataset.returnUrl || window.location.pathname;
  const completionKey = accountId ? `gobii:email_oauth_complete:${accountId}` : "gobii:email_oauth_complete";

  const defaultScopes = {
    gmail: "https://mail.google.com/",
    generic: "",
  };

  let lastDefaultScope = "";

  const managedProviders = new Set(["gmail"]);

  function setDefaultScope(scope) {
    if (!scopeInput) {
      return;
    }
    if (!scopeInput.value || scopeInput.value === lastDefaultScope) {
      scopeInput.value = scope;
    }
    lastDefaultScope = scope;
  }

  function toggleRow(row, shouldShow) {
    if (!row) {
      return;
    }
    if (shouldShow) {
      row.classList.remove("hidden");
    } else {
      row.classList.add("hidden");
    }
  }

  function updateProviderFields() {
    const provider = providerSelect ? providerSelect.value : "gmail";
    if (providerHidden) {
      providerHidden.value = provider;
    }
    toggleRow(tenantRow, false);
    toggleRow(authRow, provider === "generic");
    toggleRow(tokenRow, provider === "generic");
    const useManaged = managedProviders.has(provider);
    toggleRow(clientIdRow, !useManaged);
    toggleRow(clientSecretRow, !useManaged);
    setDefaultScope(defaultScopes[provider] || "");
    updateConnectionMode();
  }

  function updateConnectionMode() {
    const provider = providerSelect ? providerSelect.value : "gmail";
    const mode = connectionModeSelect ? connectionModeSelect.value : "custom";
    const oauthMode = mode === "oauth2";
    const showProtocols = !oauthMode || provider === "generic";
    toggleRow(panel, oauthMode);
    customOnlyFields.forEach((field) => {
      toggleRow(field, !oauthMode);
    });
    serverSettingsFields.forEach((field) => {
      if (!oauthMode) {
        toggleRow(field, true);
      } else {
        toggleRow(field, provider === "generic");
      }
    });
    protocolSections.forEach((section) => {
      toggleRow(section, showProtocols);
    });
    protocolActions.forEach((action) => {
      toggleRow(action, showProtocols);
    });
    if (oauthMode) {
      if (smtpAuthSelect) {
        smtpAuthSelect.value = "oauth2";
      }
      if (imapAuthSelect) {
        imapAuthSelect.value = "oauth2";
      }
    } else {
      if (smtpAuthSelect && smtpAuthSelect.value === "oauth2") {
        smtpAuthSelect.value = "login";
      }
      if (imapAuthSelect && imapAuthSelect.value === "oauth2") {
        imapAuthSelect.value = "login";
      }
    }
  }

  if (providerSelect) {
    providerSelect.addEventListener("change", updateProviderFields);
  }

  if (scopeInput) {
    scopeInput.addEventListener("input", () => {
      lastDefaultScope = scopeInput.value;
    });
  }

  if (connectionModeSelect) {
    connectionModeSelect.addEventListener("change", updateConnectionMode);
  }

  updateProviderFields();
  updateConnectionMode();

  function setConnectionStatus(connected) {
    if (statusEl) {
      statusEl.textContent = connected ? "Connected" : "Not connected";
    }
    if (disconnectButton) {
      if (connected) {
        disconnectButton.removeAttribute("disabled");
      } else {
        disconnectButton.setAttribute("disabled", "disabled");
      }
    }
  }

  async function refreshOAuthStatus() {
    if (!statusUrl) {
      setConnectionStatus(true);
      return;
    }
    try {
      const response = await fetch(statusUrl, {
        headers: {
          Accept: "application/json",
        },
      });
      if (!response.ok) {
        return;
      }
      const payload = await response.json();
      setConnectionStatus(Boolean(payload.connected));
    } catch (error) {
      console.warn("Failed to refresh OAuth status", error);
    }
  }

  function handleOAuthCompletion(rawValue) {
    if (!rawValue) {
      return;
    }
    try {
      const payload = JSON.parse(rawValue);
      if (payload.accountId && payload.accountId !== accountId) {
        return;
      }
      setConnectionStatus(true);
      refreshOAuthStatus();
    } catch (error) {
      console.warn("Failed to parse OAuth completion payload", error);
    }
  }

  window.addEventListener("storage", (event) => {
    if (event.key !== completionKey) {
      return;
    }
    handleOAuthCompletion(event.newValue);
  });

  function setAutoConnectFlag() {
    if (!form) {
      return;
    }
    let input = form.querySelector('input[name="auto_connect_oauth"]');
    if (!input) {
      input = document.createElement("input");
      input.type = "hidden";
      input.name = "auto_connect_oauth";
      form.appendChild(input);
    }
    input.value = "1";
  }

  function maybeStartAutoConnect() {
    const params = new URLSearchParams(window.location.search);
    const shouldAutoConnect = params.get("auto_connect_oauth");
    if (!shouldAutoConnect) {
      return;
    }
    params.delete("auto_connect_oauth");
    const nextUrl = `${window.location.pathname}${params.toString() ? `?${params.toString()}` : ""}`;
    window.history.replaceState({}, document.title, nextUrl);
    if (connectionModeSelect && connectionModeSelect.value === "oauth2") {
      startOAuth();
    }
  }

  maybeStartAutoConnect();

  function openOAuthWindow() {
    const popup = window.open("", "_blank");
    if (!popup) {
      showError(errorEl, "Allow pop-ups to open the OAuth verification tab.");
      return null;
    }
    return popup;
  }

  async function startOAuth(oauthWindow) {
    clearError(errorEl);

    if (!startUrl) {
      showError(errorEl, "OAuth start endpoint not configured.");
      if (oauthWindow) {
        oauthWindow.close();
      }
      return;
    }

    const provider = providerSelect ? providerSelect.value : "gmail";
    const useManaged = managedProviders.has(provider);
    const clientId = clientIdInput ? clientIdInput.value.trim() : "";
    const clientSecret = clientSecretInput ? clientSecretInput.value.trim() : "";
    const scope = scopeInput ? scopeInput.value.trim() : "";

    if (!useManaged && !clientId) {
      showError(errorEl, "Provide an OAuth client ID.");
      if (oauthWindow) {
        oauthWindow.close();
      }
      return;
    }

    let authorizationEndpoint = "";
    let tokenEndpoint = "";
    let extraParams = {};

    if (provider === "gmail") {
      authorizationEndpoint = "https://accounts.google.com/o/oauth2/v2/auth";
      tokenEndpoint = "https://oauth2.googleapis.com/token";
      extraParams = { access_type: "offline", prompt: "consent" };
    } else if (provider === "generic") {
      authorizationEndpoint = authInput ? authInput.value.trim() : "";
      tokenEndpoint = tokenInput ? tokenInput.value.trim() : "";
    }

    if (!authorizationEndpoint || !tokenEndpoint) {
      showError(errorEl, "Authorization and token endpoints are required.");
      if (oauthWindow) {
        oauthWindow.close();
      }
      return;
    }

    if (!scope) {
      showError(errorEl, "Provide an OAuth scope.");
      if (oauthWindow) {
        oauthWindow.close();
      }
      return;
    }

    const callbackUrl = new URL(callbackPath || window.location.pathname, window.location.origin).toString();
    const state = randomString(32);
    const codeVerifier = randomString(64);
    const codeChallenge = base64UrlEncode(await sha256(codeVerifier));

    try {
      const response = await fetch(startUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": getCsrfToken(),
        },
        body: JSON.stringify({
          account_id: accountId,
          provider,
          scope,
          token_endpoint: tokenEndpoint,
          use_gobii_app: useManaged,
          client_id: useManaged ? "" : clientId,
          client_secret: useManaged ? "" : (clientSecret || undefined),
          redirect_uri: callbackUrl,
          state,
          code_verifier: codeVerifier,
          code_challenge: codeChallenge,
          code_challenge_method: "S256",
          metadata: {
            provider,
            authorization_endpoint: authorizationEndpoint,
            token_endpoint: tokenEndpoint,
            sasl_mechanism: "XOAUTH2",
          },
        }),
      });

      if (!response.ok) {
        const detail = await response.text();
        throw new Error(detail || "Failed to start OAuth flow.");
      }

      const payload = await response.json();
      const stateKey = payload.state || state;
      const clientIdForAuth = payload.client_id || clientId;
      if (!clientIdForAuth) {
        throw new Error("OAuth client ID missing from server response.");
      }

      localStorage.setItem(
        `gobii:email_oauth_state:${stateKey}`,
        JSON.stringify({
          sessionId: payload.session_id,
          accountId,
          returnUrl,
        })
      );

      const params = new URLSearchParams({
        response_type: "code",
        client_id: clientIdForAuth,
        redirect_uri: callbackUrl,
        scope,
        state: stateKey,
        code_challenge: codeChallenge,
        code_challenge_method: "S256",
        ...extraParams,
      });

      const authUrl = `${authorizationEndpoint}?${params.toString()}`;
      if (oauthWindow) {
        if (oauthWindow.closed) {
          showError(errorEl, "OAuth tab was closed before authorization started.");
          return;
        }
        oauthWindow.location.href = authUrl;
        oauthWindow.focus();
        return;
      }
      window.location.href = authUrl;
    } catch (error) {
      if (oauthWindow && !oauthWindow.closed) {
        oauthWindow.close();
      }
      showError(errorEl, error.message || "Failed to start OAuth flow.");
    }
  }

  async function revokeOAuth() {
    clearError(errorEl);
    if (!revokeUrl) {
      showError(errorEl, "OAuth revoke endpoint not configured.");
      return;
    }
    try {
      const response = await fetch(revokeUrl, {
        method: "POST",
        headers: {
          "X-CSRFToken": getCsrfToken(),
        },
      });
      if (!response.ok) {
        const detail = await response.text();
        throw new Error(detail || "Failed to revoke OAuth.");
      }
      if (statusEl) {
        statusEl.textContent = "Not connected";
      }
      if (disconnectButton) {
        disconnectButton.setAttribute("disabled", "disabled");
      }
    } catch (error) {
      showError(errorEl, error.message || "Failed to revoke OAuth.");
    }
  }

  if (connectButton) {
    connectButton.addEventListener("click", () => {
      clearError(errorEl);
      if (endpointAddressInput) {
        if (!endpointAddressInput.value.trim()) {
          showError(errorEl, "Enter an email address to continue.");
          return;
        }
        if (!endpointAddressInput.checkValidity()) {
          endpointAddressInput.reportValidity();
          showError(errorEl, "Enter a valid email address to continue.");
          return;
        }
      }
      const oauthWindow = openOAuthWindow();
      if (!oauthWindow) {
        return;
      }
      startOAuth(oauthWindow);
    });
  }

  if (disconnectButton) {
    disconnectButton.addEventListener("click", () => {
      revokeOAuth();
    });
  }
}
