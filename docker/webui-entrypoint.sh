#!/bin/bash
set -e

source /usr/local/bin/adr-runtime-user.sh
adr_configure_runtime_user

exec gosu "$ADR_APP_USER" "$@"
