#!/bin/sh
set -eu

CONFIG_DIR="${CONFIG_DIR:-/config}"
CONFIG_FILE="${SHAIRPORT_CONFIG:-$CONFIG_DIR/shairport-sync.conf}"
MODEL_ENV="$CONFIG_DIR/model.env"

load_model_env() {
  SPS_MODEL=""
  if [ -f "$MODEL_ENV" ]; then
    while IFS='=' read -r key value; do
      case "$key" in
        SPS_MODEL)
          SPS_MODEL="$(printf '%s' "$value" | tr -d '\r')"
          ;;
      esac
    done < "$MODEL_ENV"
  fi
  if [ -z "${SPS_MODEL:-}" ]; then
    SPS_MODEL="ShairportSync"
  fi
  export SPS_MODEL
}

start_avahi_if_needed() {
  if [ -S /var/run/dbus/system_bus_socket ]; then
    echo "avahi mode: using host D-Bus/Avahi sockets; container avahi-daemon will not be started"
    return 0
  fi

  echo "avahi mode: no host D-Bus socket detected; starting container dbus-daemon and avahi-daemon"
  mkdir -p /run/dbus
  if [ ! -S /run/dbus/system_bus_socket ]; then
    dbus-daemon --system --fork
  fi
  avahi-daemon --daemonize --no-drop-root
}

start_nqptp() {
  echo "starting nqptp; it must have exclusive UDP 319/320 access for AirPlay 2 timing"
  nqptp &
  NQPTP_PID="$!"
  export NQPTP_PID
  sleep 1
  if ! kill -0 "$NQPTP_PID" 2>/dev/null; then
    echo "nqptp failed to stay running; check whether UDP 319/320 are already in use" >&2
    exit 1
  fi
}

cleanup() {
  if [ -n "${NQPTP_PID:-}" ] && kill -0 "$NQPTP_PID" 2>/dev/null; then
    kill "$NQPTP_PID" 2>/dev/null || true
  fi
}
trap cleanup INT TERM EXIT

load_model_env
echo "shairport-sync model identifier: $SPS_MODEL"

if [ ! -f "$CONFIG_FILE" ]; then
  echo "missing config file: $CONFIG_FILE" >&2
  exit 1
fi

start_avahi_if_needed
start_nqptp

echo "starting shairport-sync with config: $CONFIG_FILE"
exec shairport-sync -c "$CONFIG_FILE" -m avahi -v
