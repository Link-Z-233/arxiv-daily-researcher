#!/bin/bash
set -e

source /usr/local/bin/adr-runtime-user.sh
adr_configure_runtime_user
for APP_DIRECTORY in /app/data /app/data/daily_research /app/logs /app/configs /app/runtime /app/data/run/webui_triggers; do
    adr_prepare_writable_directory "$APP_DIRECTORY"
done
adr_require_writable_file_if_present /app/.env

# Keep v4.1 installations working without a manual configuration move.
adr_run_as_user bash -c \
    'cd /app && PYTHONPATH=/app/src exec /usr/local/bin/python -c "from utils.config_io import ensure_runtime_config_path; ensure_runtime_config_path()"'

exec gosu "$ADR_APP_USER" "$@"
