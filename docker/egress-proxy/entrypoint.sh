#!/bin/sh
# Builds the gost command from environment variables and exec's it.
# Required: UPSTREAM_PROXY_SCHEME, UPSTREAM_HOST, UPSTREAM_PORT
# Optional: UPSTREAM_USERNAME, UPSTREAM_PASSWORD, HTTP_LISTEN_PORT, SOCKS_LISTEN_PORT

set -eu

HTTP_LISTEN_PORT="${HTTP_LISTEN_PORT:-3128}"
SOCKS_LISTEN_PORT="${SOCKS_LISTEN_PORT:-1080}"
UPSTREAM_PROXY_SCHEME="${UPSTREAM_PROXY_SCHEME:-}"
UPSTREAM_HOST="${UPSTREAM_HOST:-}"
UPSTREAM_PORT="${UPSTREAM_PORT:-}"
UPSTREAM_USERNAME="${UPSTREAM_USERNAME:-}"
UPSTREAM_PASSWORD="${UPSTREAM_PASSWORD:-}"

if [ -z "$UPSTREAM_PROXY_SCHEME" ] || [ -z "$UPSTREAM_HOST" ] || [ -z "$UPSTREAM_PORT" ]; then
    echo "ERROR: UPSTREAM_PROXY_SCHEME, UPSTREAM_HOST, and UPSTREAM_PORT are required" >&2
    exit 1
fi

# Percent-encode a userinfo component value (username or password).
# Encodes all RFC 3986 reserved characters that could break URL structure.
encode_credential() {
    printf '%s' "$1" | sed \
        -e 's/%/%25/g' \
        -e 's/ /%20/g' \
        -e 's/!/%21/g' \
        -e 's/#/%23/g' \
        -e 's/\$/%24/g' \
        -e 's/&/%26/g' \
        -e "s/'/%27/g" \
        -e 's/(/%28/g' \
        -e 's/)/%29/g' \
        -e 's/\*/%2A/g' \
        -e 's/+/%2B/g' \
        -e 's/,/%2C/g' \
        -e 's|/|%2F|g' \
        -e 's/:/%3A/g' \
        -e 's/;/%3B/g' \
        -e 's/=/%3D/g' \
        -e 's/?/%3F/g' \
        -e 's/@/%40/g' \
        -e 's/\[/%5B/g' \
        -e 's/\]/%5D/g'
}

if [ -n "$UPSTREAM_USERNAME" ] && [ -n "$UPSTREAM_PASSWORD" ]; then
    ENCODED_USER="$(encode_credential "$UPSTREAM_USERNAME")"
    ENCODED_PASS="$(encode_credential "$UPSTREAM_PASSWORD")"
    UPSTREAM_URL="${UPSTREAM_PROXY_SCHEME}://${ENCODED_USER}:${ENCODED_PASS}@${UPSTREAM_HOST}:${UPSTREAM_PORT}"
elif [ -n "$UPSTREAM_USERNAME" ]; then
    ENCODED_USER="$(encode_credential "$UPSTREAM_USERNAME")"
    UPSTREAM_URL="${UPSTREAM_PROXY_SCHEME}://${ENCODED_USER}@${UPSTREAM_HOST}:${UPSTREAM_PORT}"
else
    UPSTREAM_URL="${UPSTREAM_PROXY_SCHEME}://${UPSTREAM_HOST}:${UPSTREAM_PORT}"
fi

exec gost \
    -L "http://0.0.0.0:${HTTP_LISTEN_PORT}" \
    -L "socks5://0.0.0.0:${SOCKS_LISTEN_PORT}" \
    -F "$UPSTREAM_URL"
