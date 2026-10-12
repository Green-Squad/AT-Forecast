#!/bin/sh
set -eu
[ "${HAND_FORGED_DEMO:-}" = true ] || { echo 'Demo mode is required' >&2; exit 1; }
export FASTATWEATHER_DATABASE_HOST=127.0.0.1 FASTATWEATHER_DATABASE_USERNAME=handforged FASTATWEATHER_DATABASE_PASSWORD=HandForgedLocalDatabaseOnly
export SECRET_KEY_BASE="$(openssl rand -hex 64)" DEVISE_SECRET_KEY="$(openssl rand -hex 64)"
unset OPEN_WEATHER_MAP_API_KEY GOOGLE_MAPS_API_KEY
# The sidecar TCP probe passes only after its initialization scripts finish.
# Check actual seed contents too, so the web listener never opens onto an empty database.
bundle exec ruby hand-forged/wait_for_database.rb
rm -rf public/cached_pages/*
mkdir -p public/cached_pages/shelters
bundle exec rails server -b 0.0.0.0 -p "${PORT:-8080}" &
web_pid=$!
shutdown() {
 trap - TERM INT
 kill -INT "$web_pid" 2>/dev/null || true
 wait "$web_pid" || true
 exit 0
}
trap shutdown TERM INT
wait "$web_pid"
