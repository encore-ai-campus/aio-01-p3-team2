#!/bin/sh
set -eu
umask 077
# Validate values before Caddyfile environment substitution. Never print hashes/proofs.
fail() { echo 'Production web configuration is invalid' >&2; exit 1; }
case "${SERVICE_ROLE:-}" in
    plan-web) export WEB_SITE=plan ;;
    game-web) export WEB_SITE=game ;;
    *) fail ;;
esac
case "${DEPLOYMENT_MODE:-}" in cloud|verification) ;; *) fail ;; esac
printf '%s' "${PUBLIC_DOMAIN:-}" | grep -Eq '^[a-z0-9][a-z0-9.-]+\.[a-z]+$' || fail
case "$DEPLOYMENT_MODE:$PUBLIC_DOMAIN" in cloud:*.localhost|cloud:*.internal) fail ;; esac
case "$DEPLOYMENT_MODE:$PUBLIC_DOMAIN" in verification:*.localhost) ;; verification:*) fail ;; esac
for name in OPENAI_API_KEY DATABASE_URL MCP_DATABASE_URL MIGRATION_DATABASE_URL RDS_BOOTSTRAP_DATABASE_URL AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN; do
    eval 'value=${'"$name"':-}'
    [ -z "$value" ] || fail
done
printf '%s' "${ACME_EMAIL:-}" | grep -Eq '^[A-Za-z0-9._+-]+@[A-Za-z0-9.-]+\.[A-Za-z]+$' || fail
printf '%s' "${WEB_USERNAME:-}" | grep -Eq '^[A-Za-z0-9_-]{3,64}$' || fail
printf '%s' "${WEB_PASSWORD_HASH:-}" | grep -Eq '^\$2[aby]\$[0-9]{2}\$[./A-Za-z0-9]{53}$' || fail
check_upstream() {
    printf '%s' "$1" | grep -Eq '^http://([a-z0-9][a-z0-9.-]+\.internal|10\.[0-9]+\.[0-9]+\.[0-9]+|192\.168\.[0-9]+\.[0-9]+|172\.(1[6-9]|2[0-9]|3[01])\.[0-9]+\.[0-9]+):8000$' || fail
}
check_upstream "${MAIN_API_UPSTREAM:-}"
printf '%s' "${MAIN_PROXY_KEY:-}" | grep -Eq '^[A-Za-z0-9_-]{32,128}$' || fail
if [ "$SERVICE_ROLE" = plan-web ]; then
    check_upstream "${FAULT_API_UPSTREAM:-}"
    printf '%s' "${FAULT_PROXY_KEY:-}" | grep -Eq '^[A-Za-z0-9_-]{32,128}$' || fail
    [ "$MAIN_PROXY_KEY" != "$FAULT_PROXY_KEY" ] && [ "$MAIN_API_UPSTREAM" != "$FAULT_API_UPSTREAM" ] || fail
else
    [ -z "${FAULT_PROXY_KEY:-}" ] && [ -z "${FAULT_API_UPSTREAM:-}" ] || fail
fi
for directory in /data /config; do
    probe=$(mktemp "$directory/.eventquest-write-check.XXXXXX") || fail
    rm "$probe"
done
exec caddy run --config /etc/caddy/Caddyfile --adapter caddyfile
