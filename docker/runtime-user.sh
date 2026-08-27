#!/bin/bash

# Shared runtime-user setup for the worker and Streamlit containers.  The
# entrypoints stay root only long enough to prepare bind mounts and (for the
# worker) install cron; application processes run as this unprivileged user.

ADR_APP_USER="adr"
ADR_APP_GROUP="adr"

_adr_validate_id() {
    local name="$1"
    local value="$2"
    if ! [[ "$value" =~ ^[1-9][0-9]*$ ]] || [ "$value" -gt 2147483647 ]; then
        echo "ERROR: $name must be an integer between 1 and 2147483647 (got: $value)" >&2
        return 1
    fi
}

adr_configure_runtime_user() {
    if [ "$(id -u)" -ne 0 ]; then
        echo "ERROR: the container entrypoint must start as root so it can map PUID/PGID" >&2
        return 1
    fi

    PUID="${PUID:-1000}"
    PGID="${PGID:-1000}"
    _adr_validate_id PUID "$PUID"
    _adr_validate_id PGID "$PGID"

    local current_gid current_uid
    current_gid="$(id -g "$ADR_APP_USER")"
    current_uid="$(id -u "$ADR_APP_USER")"
    if [ "$current_gid" != "$PGID" ]; then
        groupmod --non-unique --gid "$PGID" "$ADR_APP_GROUP"
    fi
    if [ "$current_uid" != "$PUID" ]; then
        usermod --non-unique --uid "$PUID" --gid "$PGID" "$ADR_APP_USER"
    else
        usermod --gid "$PGID" "$ADR_APP_USER"
    fi

    # Repair files produced by older root-running images and make new output
    # inherit the NAS user's numeric ownership.  These paths are the only
    # writable bind mounts used by the application.
    local path
    for path in /app/data /app/logs /app/configs; do
        if [ -e "$path" ]; then
            chown -R "$PUID:$PGID" "$path"
        fi
    done
    if [ -e /app/.env ]; then
        chown "$PUID:$PGID" /app/.env
    fi

    export PUID PGID HOME=/app
    echo "Runtime user: $ADR_APP_USER ($PUID:$PGID)"
}

adr_run_as_user() {
    gosu "$ADR_APP_USER" "$@"
}
