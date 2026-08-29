#!/bin/bash
set -e

source /usr/local/bin/adr-runtime-user.sh
adr_configure_runtime_user
for APP_DIRECTORY in /app/data /app/data/daily_research /app/logs /app/configs /app/data/run/webui_triggers; do
    adr_prepare_writable_directory "$APP_DIRECTORY"
done
adr_require_writable_file_if_present /app/.env

exec gosu "$ADR_APP_USER" "$@"
