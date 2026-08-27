#!/bin/bash

# Shared runtime-user setup for the worker and Streamlit containers. The
# entrypoints stay root only for in-container account/cron setup; every normal
# operation on a host bind mount runs as the configured NAS user.

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

    # Recursive ownership changes on every start are expensive on NAS volumes,
    # can fail on SMB/NFS mounts, and mean PID 1 writes the host bind mount as
    # root. Existing installations can opt in once to repair files created by
    # old root-running images; routine starts only verify the mapped user can
    # write the paths it needs.
    case "${ADR_REPAIR_OWNERSHIP:-false}" in
        1|true|TRUE|yes|YES|on|ON)
            echo "Repairing bind-mount ownership once for $PUID:$PGID..."
            local path
            for path in /app/data /app/logs /app/configs /app/.env; do
                if [ -e "$path" ]; then
                    chown -R "$PUID:$PGID" "$path"
                fi
            done
            ;;
        0|false|FALSE|no|NO|off|OFF|"")
            ;;
        *)
            echo "ERROR: ADR_REPAIR_OWNERSHIP must be true or false" >&2
            return 1
            ;;
    esac

    export PUID PGID HOME=/app
    echo "Runtime user: $ADR_APP_USER ($PUID:$PGID)"
}

adr_run_as_user() {
    gosu "$ADR_APP_USER" "$@"
}

adr_prepare_writable_directory() {
    local path="$1"
    local probe
    if ! adr_run_as_user mkdir -p "$path"; then
        echo "ERROR: runtime user $PUID:$PGID cannot create $path. " \
            "Set PUID/PGID to the NAS owner, or run once with ADR_REPAIR_OWNERSHIP=true." >&2
        return 1
    fi
    if ! probe="$(adr_run_as_user mktemp "$path/.adr-write-check.XXXXXX")"; then
        echo "ERROR: runtime user $PUID:$PGID cannot write $path. " \
            "Set PUID/PGID to the NAS owner, or run once with ADR_REPAIR_OWNERSHIP=true." >&2
        return 1
    fi
    adr_run_as_user rm -f "$probe"
}

adr_require_writable_file_if_present() {
    local path="$1"
    if [ -e "$path" ] && ! adr_run_as_user test -w "$path"; then
        echo "ERROR: runtime user $PUID:$PGID cannot write $path. " \
            "Set PUID/PGID to the NAS owner, or run once with ADR_REPAIR_OWNERSHIP=true." >&2
        return 1
    fi
}
