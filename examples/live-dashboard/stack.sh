#!/usr/bin/env bash
# Start, stop or describe this user's live dashboard: the services in
# compose.yaml beside this script, with ports and secrets of the user's own.
#
#     stack.sh up       start it, and print where Grafana is and how agents send
#     stack.sh env      print the environment an agent process needs
#     stack.sh down     stop it (down -v also drops Prometheus's and Grafana's data)
#
# Each user gets ten ports from 20000 + (uid % 4000) * 10: Grafana on the
# first, the collector's OTLP/HTTP and OTLP/gRPC on the next two, so users of
# one node do not collide. GRAFANA_PORT, OTLP_HTTP_PORT and OTLP_GRPC_PORT set
# them; BIND_ADDRESS (127.0.0.1) the interface. The Grafana admin password and
# the collector's token are made once and kept, readable by the user only, in
# ${XDG_STATE_HOME:-~/.local/state}/flowgentic-live; GRAFANA_ADMIN_PASSWORD and
# OTLP_TOKEN set them instead. GRAFANA_ANONYMOUS=true lets anyone who reaches
# Grafana view the dashboards without a login.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
state=${FLOWGENTIC_LIVE_STATE:-${XDG_STATE_HOME:-$HOME/.local/state}/flowgentic-live}
mkdir -p "$state"
chmod 700 "$state"

secret() {  # a random secret kept in $state/$1, made on first use
	local file="$state/$1"
	if [ ! -s "$file" ]; then
		(umask 077 && openssl rand -hex 24 > "$file")
	fi
	cat "$file"
}

base=$((20000 + ($(id -u) % 4000) * 10))
export BIND_ADDRESS=${BIND_ADDRESS:-127.0.0.1}
export GRAFANA_PORT=${GRAFANA_PORT:-$base}
export OTLP_HTTP_PORT=${OTLP_HTTP_PORT:-$((base + 1))}
export OTLP_GRPC_PORT=${OTLP_GRPC_PORT:-$((base + 2))}
export GRAFANA_ADMIN_PASSWORD=${GRAFANA_ADMIN_PASSWORD:-$(secret grafana-admin-password)}
export OTLP_TOKEN=${OTLP_TOKEN:-$(secret otlp-token)}
host=$BIND_ADDRESS
if [ "$host" = 0.0.0.0 ]; then
	host=$(hostname)
fi

compose() {
	docker compose -p "flowgentic-live-$(id -un)" -f "$here/compose.yaml" "$@"
}

agent_env() {
	echo "export OTEL_EXPORTER_OTLP_ENDPOINT=http://$host:$OTLP_HTTP_PORT"
	echo "export OTEL_EXPORTER_OTLP_HEADERS=Authorization=Bearer%20$OTLP_TOKEN"
}

case "${1:-up}" in
up)
	compose up -d
	(umask 077 && agent_env > "$state/agents.env")
	echo "Grafana: http://$host:$GRAFANA_PORT (admin, password in $state/grafana-admin-password)"
	echo "Agent processes: source $state/agents.env, then flowgentic.otlp.configure() with no endpoint"
	;;
env)
	agent_env
	;;
down)
	shift
	compose down "$@"
	;;
*)
	echo "usage: $0 up | env | down [-v]" >&2
	exit 2
	;;
esac
