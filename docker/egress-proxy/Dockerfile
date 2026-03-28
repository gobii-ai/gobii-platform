FROM golang:1.22-alpine AS builder

# Pin to a specific gost release tag for reproducibility.
ARG GOST_VERSION=v2.12.0

RUN apk add --no-cache git \
    && git clone --depth 1 --branch "${GOST_VERSION}" \
        https://github.com/ginuerzh/gost.git /src/gost \
    && cd /src/gost \
    && CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /gost ./cmd/gost

FROM alpine:3.21

RUN apk add --no-cache ca-certificates

COPY --from=builder /gost /usr/local/bin/gost
RUN chmod 755 /usr/local/bin/gost

COPY entrypoint.sh /entrypoint.sh
RUN chmod 755 /entrypoint.sh

# Run as non-root to match the pod security context (runAsUser: 1000)
RUN addgroup -g 1000 proxy && adduser -u 1000 -G proxy -S proxy

USER 1000:1000

# HTTP proxy port and SOCKS5 port; overridden via env in the pod manifest
EXPOSE 3128 1080

ENTRYPOINT ["/entrypoint.sh"]
