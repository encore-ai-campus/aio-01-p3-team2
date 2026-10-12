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
if [ -n "${PUBLIC_HOST:-}" ] && [ -n "${PUBLIC_DOMAIN:-}" ]; then
    [ "$PUBLIC_HOST" = "$PUBLIC_DOMAIN" ] || fail
fi
export PUBLIC_HOST=${PUBLIC_HOST:-${PUBLIC_DOMAIN:-}}
case "${WEB_ACCESS_MODE:-domain}" in
    ip)
        [ "$DEPLOYMENT_MODE" = cloud ] || fail
        [ "${HTTPS_MODE:-}" = internal ] || fail
        printf '%s' "$PUBLIC_HOST" | awk -F. '
          NF != 4 { exit 1 }
          { for(i=1;i<=4;i++) if ($i !~ /^[0-9]+$/ || $i > 255 || (length($i)>1 && substr($i,1,1)=="0")) exit 1 }
          $1==0 || $1==10 || $1==127 || $1>=224 { exit 1 }
          $1==169 && $2==254 { exit 1 }
          $1==172 && $2>=16 && $2<=31 { exit 1 }
          $1==192 && ($2==168 || $2==0 || ($2==0 && $3==2)) { exit 1 }
          $1==100 && $2>=64 && $2<=127 { exit 1 }
          $1==198 && ($2==18 || $2==19 || ($2==51 && $3==100)) { exit 1 }
          $1==203 && $2==0 && $3==113 { exit 1 }
        ' || fail
        ;;
    domain)
        printf '%s' "$PUBLIC_HOST" | grep -Eq '^[a-z0-9][a-z0-9.-]+\.[a-z]+$' || fail
        case "$DEPLOYMENT_MODE:$PUBLIC_HOST" in cloud:*.localhost|cloud:*.internal) fail ;; esac
        case "$DEPLOYMENT_MODE:$PUBLIC_HOST" in verification:*.localhost) ;; verification:*) fail ;; esac
        ;;
    *) fail ;;
esac
if [ "$DEPLOYMENT_MODE" = verification ]; then
    export HTTPS_MODE=${HTTPS_MODE:-internal}
fi
case "${HTTPS_MODE:-acme}" in
    internal) export TLS_CONFIGURATION='tls internal' ;;
    acme)
        printf '%s' "${ACME_EMAIL:-}" | grep -Eq '^[A-Za-z0-9._+-]+@[A-Za-z0-9.-]+\.[A-Za-z]+$' || fail
        export TLS_CONFIGURATION="tls $ACME_EMAIL"
        ;;
    *) fail ;;
esac
for name in OPENAI_API_KEY DATABASE_URL MCP_DATABASE_URL MIGRATION_DATABASE_URL RDS_BOOTSTRAP_DATABASE_URL AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN; do
    eval 'value=${'"$name"':-}'
    [ -z "$value" ] || fail
done
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
