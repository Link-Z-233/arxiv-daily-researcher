"""Local authentication gate for the Streamlit configuration panel.

The panel is deliberately a single-administrator application: it can modify
API credentials and enqueue workloads, so a broad multi-user permission model
would add complexity without making the default NAS deployment safer. Passwords
are stored only as salted PBKDF2-HMAC hashes in ``.env``. The record uses
colon separators so Docker Compose never interprets it as variable syntax.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import re
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Mapping, MutableMapping, Optional

import streamlit as st

from utils.config_io import write_env
from webui.i18n import t


_HASH_SCHEME = "pbkdf2_sha256"
_PBKDF2_ITERATIONS = 600_000
_MIN_PASSWORD_LENGTH = 12
_USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,63}$")
_SESSION_AUTHENTICATED = "_webui_authenticated"
_SESSION_LAST_ACTIVITY = "_webui_auth_last_activity"
_ATTEMPT_WINDOW_SECONDS = 15 * 60
_attempt_lock = threading.Lock()
_attempt_state: dict[str, tuple[int, float, float]] = {}


@dataclass(frozen=True)
class WebUIAuthConfig:
    """Normalized auth configuration read from the `.env` map."""

    enabled: bool
    username: str
    password_hash: str
    session_timeout_minutes: int


def _as_bool(value: object, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _bounded_int(value: object, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return parsed if minimum <= parsed <= maximum else default


def read_auth_config(env_values: Mapping[str, object]) -> WebUIAuthConfig:
    """Read safe defaults without ever rendering the configured hash."""
    username = str(env_values.get("WEBUI_ADMIN_USERNAME", "")).strip()
    password_hash = str(env_values.get("WEBUI_ADMIN_PASSWORD_HASH", "")).strip()
    return WebUIAuthConfig(
        enabled=_as_bool(env_values.get("WEBUI_AUTH_ENABLED"), True),
        username=username,
        password_hash=password_hash,
        session_timeout_minutes=_bounded_int(
            env_values.get("WEBUI_SESSION_TIMEOUT_MINUTES"),
            480,
            minimum=5,
            maximum=10_080,
        ),
    )


def validate_username(username: str) -> Optional[str]:
    """Return an i18n key when the username cannot be stored safely."""
    if not _USERNAME_PATTERN.fullmatch((username or "").strip()):
        return "auth_username_invalid"
    return None


def validate_password(password: str) -> Optional[str]:
    """Keep password requirements strong but passphrase-friendly."""
    if len(password or "") < _MIN_PASSWORD_LENGTH:
        return "auth_password_too_short"
    return None


def hash_password(password: str) -> str:
    """Create a versioned PBKDF2-HMAC record suitable for `.env` storage."""
    error_key = validate_password(password)
    if error_key:
        raise ValueError(error_key)
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS
    )
    return ":".join(
        (
            _HASH_SCHEME,
            str(_PBKDF2_ITERATIONS),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(digest).decode("ascii"),
        )
    )


def _parse_password_hash(value: str) -> Optional[tuple[int, bytes, bytes]]:
    try:
        scheme, raw_iterations, encoded_salt, encoded_digest = value.split(":", 3)
        iterations = int(raw_iterations)
        if scheme != _HASH_SCHEME or not 100_000 <= iterations <= 1_000_000:
            return None
        salt = base64.urlsafe_b64decode(encoded_salt.encode("ascii"))
        digest = base64.urlsafe_b64decode(encoded_digest.encode("ascii"))
        if len(salt) < 16 or len(digest) != 32:
            return None
        return iterations, salt, digest
    except (AttributeError, ValueError, UnicodeEncodeError, binascii.Error):
        return None


def verify_password_hash(password_hash: str, password: str) -> Optional[bool]:
    """Verify a record, returning ``None`` when its format is invalid."""
    parsed = _parse_password_hash(password_hash)
    if parsed is None:
        return None
    iterations, salt, expected_digest = parsed
    actual_digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations
    )
    return hmac.compare_digest(actual_digest, expected_digest)


def _configured(config: WebUIAuthConfig) -> bool:
    return bool(
        _USERNAME_PATTERN.fullmatch(config.username)
        and verify_password_hash(config.password_hash, "") is not None
    )


def _clear_session(session: MutableMapping[str, object]) -> None:
    for key in (
        _SESSION_AUTHENTICATED,
        _SESSION_LAST_ACTIVITY,
    ):
        session.pop(key, None)


def _is_authenticated(config: WebUIAuthConfig) -> bool:
    if not st.session_state.get(_SESSION_AUTHENTICATED, False):
        return False
    now = time.time()
    last_activity = st.session_state.get(_SESSION_LAST_ACTIVITY)
    if not isinstance(last_activity, (int, float)) or (
        now - last_activity > config.session_timeout_minutes * 60
    ):
        _clear_session(st.session_state)
        return False
    st.session_state[_SESSION_LAST_ACTIVITY] = now
    return True


def _retry_delay_seconds(failures: int) -> int:
    """Slow repeated attempts without turning ordinary typos into a lockout."""
    if failures < 5:
        return 0
    return min(60, 2 ** min(6, failures - 5))


def _remaining_retry_seconds(username: str) -> int:
    """Return a process-wide delay for the single administrator account.

    Streamlit sessions are cheap to reconnect, so session-only counters would
    not protect a public reverse proxy. A small in-process account limiter
    covers all panel sessions; a restart clears it, which is acceptable for a
    locally bound management interface and avoids persisting any login data.
    """
    now = time.time()
    with _attempt_lock:
        state = _attempt_state.get(username)
        if state is None:
            return 0
        _failures, last_failure, retry_after = state
        if now - last_failure > _ATTEMPT_WINDOW_SECONDS:
            _attempt_state.pop(username, None)
            return 0
        return max(0, int(retry_after - now))


def _record_failed_attempt(username: str) -> None:
    now = time.time()
    with _attempt_lock:
        failures, last_failure, _retry_after = _attempt_state.get(
            username, (0, 0.0, 0.0)
        )
        if now - last_failure > _ATTEMPT_WINDOW_SECONDS:
            failures = 0
        failures += 1
        delay = _retry_delay_seconds(failures)
        _attempt_state[username] = (failures, now, now + delay if delay else 0.0)


def _clear_attempts(username: str) -> None:
    with _attempt_lock:
        _attempt_state.pop(username, None)


def _save_admin_account(env_values: Mapping[str, object], username: str, password: str) -> None:
    updated = dict(env_values)
    updated.update(
        {
            "WEBUI_AUTH_ENABLED": "true",
            "WEBUI_ADMIN_USERNAME": username,
            "WEBUI_ADMIN_PASSWORD_HASH": hash_password(password),
        }
    )
    write_env(updated)
    st.cache_data.clear()


def _render_first_setup(env_values: Mapping[str, object]) -> None:
    st.title(t("auth_setup_title"))
    st.info(t("auth_setup_notice"))
    with st.form("webui_auth_initial_setup", clear_on_submit=False):
        username = st.text_input(t("auth_username"), key="webui_auth_setup_username")
        password = st.text_input(
            t("auth_password"), type="password", key="webui_auth_setup_password"
        )
        password_again = st.text_input(
            t("auth_password_confirm"),
            type="password",
            key="webui_auth_setup_password_confirm",
        )
        submitted = st.form_submit_button(t("auth_create_account"), type="primary")
    if not submitted:
        return

    validation_error = validate_username(username) or validate_password(password)
    if validation_error:
        st.error(t(validation_error))
    elif password != password_again:
        st.error(t("auth_password_mismatch"))
    else:
        _save_admin_account(env_values, username.strip(), password)
        _clear_session(st.session_state)
        st.success(t("auth_setup_success"))
        st.rerun()


def require_authentication(env_values: Mapping[str, object]) -> bool:
    """Render the gate and return whether the current Streamlit session may continue."""
    config = read_auth_config(env_values)
    if not config.enabled:
        return True
    if not _configured(config):
        _render_first_setup(env_values)
        return False
    if _is_authenticated(config):
        return True

    st.title(t("auth_login_title"))
    st.caption(t("auth_login_hint"))
    remaining = _remaining_retry_seconds(config.username)
    if remaining:
        st.warning(t("auth_retry_wait").format(seconds=remaining))

    with st.form("webui_auth_login", clear_on_submit=False):
        username = st.text_input(t("auth_username"), key="webui_auth_username")
        password = st.text_input(
            t("auth_password"), type="password", key="webui_auth_password"
        )
        submitted = st.form_submit_button(
            t("auth_login"), type="primary", disabled=bool(remaining)
        )
    if not submitted:
        return False

    username_ok = hmac.compare_digest(username.strip(), config.username)
    password_ok = verify_password_hash(config.password_hash, password)
    if username_ok and password_ok is True:
        st.session_state[_SESSION_AUTHENTICATED] = True
        st.session_state[_SESSION_LAST_ACTIVITY] = time.time()
        _clear_attempts(config.username)
        st.session_state.pop("webui_auth_password", None)
        st.rerun()

    _record_failed_attempt(config.username)
    st.session_state.pop("webui_auth_password", None)
    st.error(t("auth_login_failed"))
    return False


def render_account_controls(env_values: Mapping[str, object]) -> None:
    """Render logout and password rotation controls in the authenticated sidebar."""
    config = read_auth_config(env_values)
    if not config.enabled:
        return
    if st.button(t("auth_logout"), key="webui_auth_logout", width="stretch"):
        _clear_session(st.session_state)
        st.rerun()

    with st.expander(t("auth_change_password"), expanded=False):
        with st.form("webui_auth_change_password", clear_on_submit=True):
            current_password = st.text_input(
                t("auth_current_password"), type="password"
            )
            new_password = st.text_input(t("auth_new_password"), type="password")
            new_password_again = st.text_input(t("auth_password_confirm"), type="password")
            submitted = st.form_submit_button(t("auth_save_password"))
        if not submitted:
            return
        if verify_password_hash(config.password_hash, current_password) is not True:
            st.error(t("auth_current_password_invalid"))
        elif validation_error := validate_password(new_password):
            st.error(t(validation_error))
        elif new_password != new_password_again:
            st.error(t("auth_password_mismatch"))
        else:
            _save_admin_account(env_values, config.username, new_password)
            _clear_session(st.session_state)
            st.success(t("auth_password_changed"))
