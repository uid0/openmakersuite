#!/bin/sh
# Activate the ForgeKey mTLS listener template when FORGEKEY_MTLS_ENABLED=1.
#
# The template ships as `mtls.conf.template.disabled` so nginx's normal
# entrypoint ignores it by default; if you renamed it eagerly, nginx would
# fail to start in deployments that haven't yet mounted the CA cert at
# FORGEKEY_MTLS_CA_PATH.
#
# This script runs BEFORE nginx's 20-envsubst-on-templates.sh, so the
# rename happens in time for envsubst to render `mtls.conf` into
# /etc/nginx/conf.d/.
set -eu

if [ "${FORGEKEY_MTLS_ENABLED:-0}" != "1" ]; then
    exit 0
fi

: "${FORGEKEY_MTLS_PORT:=8443}"
export FORGEKEY_MTLS_PORT

if [ -z "${FORGEKEY_MTLS_CA_PATH:-}" ]; then
    echo "20-mtls: FORGEKEY_MTLS_ENABLED=1 but FORGEKEY_MTLS_CA_PATH is unset." >&2
    echo "20-mtls: bind-mount the OMS CA PEM into the nginx container and set the path." >&2
    exit 1
fi
if [ ! -r "$FORGEKEY_MTLS_CA_PATH" ]; then
    echo "20-mtls: FORGEKEY_MTLS_CA_PATH=$FORGEKEY_MTLS_CA_PATH is not readable." >&2
    exit 1
fi
export FORGEKEY_MTLS_CA_PATH

disabled=/etc/nginx/templates/mtls.conf.template.disabled
enabled=/etc/nginx/templates/mtls.conf.template
if [ -f "$disabled" ] && [ ! -f "$enabled" ]; then
    mv "$disabled" "$enabled"
    echo "20-mtls: mTLS template activated on :$FORGEKEY_MTLS_PORT (CA=$FORGEKEY_MTLS_CA_PATH)"
fi
