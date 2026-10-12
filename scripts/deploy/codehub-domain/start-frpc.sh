#!/bin/sh
# .env 是受信任的本地 shell 配置；真实凭据始终留在仓库外。
set -eu
deployment_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ ! -f "$deployment_dir/.env" ]; then
    echo 'Missing deployment .env beside start-frpc.sh' >&2
    exit 1
fi
set -a
. "$deployment_dir/.env"
set +a
: "${FRP_AUTH_TOKEN:?Missing FRP_AUTH_TOKEN}"
: "${FRP_SERVER_ADDR:?Missing FRP_SERVER_ADDR}"
: "${CODEHUB_LOCAL_PORT:?Missing CODEHUB_LOCAL_PORT}"
if [ "${1:-}" = verify ]; then
    exec "${FRPC_BIN:-frpc}" verify -c "$deployment_dir/frpc.toml"
fi
exec "${FRPC_BIN:-frpc}" -c "$deployment_dir/frpc.toml"
