#!/bin/sh
set -eu

IMAGE=${ASTRO_WORKER_IMAGE:-}
COMMIT=${GIT_COMMIT:-}
COMPOSE_FILE=${ASTRO_WORKER_COMPOSE_FILE:-deploy/compose.worker.yml}
OIDC_ISSUER=https://token.actions.githubusercontent.com
STABLE_CERTIFICATE_IDENTITY='^https://github.com/MikhailXiaomaikou/Standard-Astro/.github/workflows/worker-image.yml@refs/tags/v[^/]+$'
FOUNDRY_CERTIFICATE_IDENTITY='^https://github.com/MikhailXiaomaikou/Standard-Astro/.github/workflows/foundry-formal-worker.yml@refs/heads/main$'

require_full_commit() {
  value=$1
  label=$2
  if [ "${#value}" -ne 40 ]; then
    echo "$label must be a 40-character release commit" >&2
    exit 2
  fi
  case "$value" in
    *[!0-9a-f]*)
      echo "$label must be lowercase hexadecimal" >&2
      exit 2
      ;;
  esac
}

if [ -z "$IMAGE" ]; then
  echo "ASTRO_WORKER_IMAGE must name the official image by digest" >&2
  exit 2
fi
case "$IMAGE" in
  *@sha256:*) ;;
  *)
    echo "ASTRO_WORKER_IMAGE must use image@sha256:<64 hex>" >&2
    exit 2
    ;;
esac
DIGEST=${IMAGE##*@sha256:}
if [ "${#DIGEST}" -ne 64 ]; then
  echo "ASTRO_WORKER_IMAGE contains an invalid digest" >&2
  exit 2
fi
case "$DIGEST" in
  *[!0-9a-f]*)
    echo "ASTRO_WORKER_IMAGE digest must be lowercase hexadecimal" >&2
    exit 2
    ;;
esac
if [ -n "$COMMIT" ]; then
  require_full_commit "$COMMIT" GIT_COMMIT
fi
if ! command -v cosign >/dev/null 2>&1; then
  echo "cosign is required to verify the official Worker signature" >&2
  exit 2
fi
if ! command -v docker >/dev/null 2>&1; then
  echo "Docker with Compose v2 is required" >&2
  exit 2
fi

if ! cosign verify "$IMAGE" \
  --certificate-identity-regexp "$STABLE_CERTIFICATE_IDENTITY" \
  --certificate-oidc-issuer "$OIDC_ISSUER" >/dev/null 2>&1; then
  cosign verify "$IMAGE" \
    --certificate-identity-regexp "$FOUNDRY_CERTIFICATE_IDENTITY" \
    --certificate-oidc-issuer "$OIDC_ISSUER" >/dev/null
fi

export WORKER_IMAGE_DIGEST="sha256:$DIGEST"
docker compose -f "$COMPOSE_FILE" pull science-worker

# TOOL_VERSION is baked into both allowlisted Worker build workflows. Never let
# a host-provided GIT_COMMIT replace that attested code identity. An optional
# GIT_COMMIT is only an expected-value cross-check for a release manifest.
if ! IMAGE_ENV=$(docker image inspect "$IMAGE" --format '{{range .Config.Env}}{{println .}}{{end}}'); then
  echo "Could not inspect the pulled Worker image" >&2
  exit 2
fi
IMAGE_COMMIT=$(printf '%s\n' "$IMAGE_ENV" | sed -n 's/^TOOL_VERSION=//p' | tail -n 1)
require_full_commit "$IMAGE_COMMIT" "Worker image TOOL_VERSION"
if [ -n "$COMMIT" ] && [ "$COMMIT" != "$IMAGE_COMMIT" ]; then
  echo "GIT_COMMIT does not match the signed Worker image TOOL_VERSION" >&2
  exit 2
fi

if [ "$#" -eq 0 ] || [ "${1:-}" = "start" ]; then
  docker compose -f "$COMPOSE_FILE" up -d science-worker
else
  docker compose -f "$COMPOSE_FILE" run --rm science-worker "$@"
fi
