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
import datetime as dt
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Mapping, MutableMapping, Optional

import streamlit as st
from extra_streamlit_components import CookieManager

from utils.config_io import write_env
from webui.i18n import t


_HASH_SCHEME = "pbkdf2_sha256"
_PBKDF2_ITERATIONS = 600_000
_MIN_PASSWORD_LENGTH = 6
_USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,63}$")
_SESSION_AUTHENTICATED = "_webui_authenticated"
_SESSION_LAST_ACTIVITY = "_webui_auth_last_activity"
_SESSION_PENDING_COOKIE = "_webui_auth_pending_cookie"
_SESSION_PENDING_COOKIE_CLEAR = "_webui_auth_pending_cookie_clear"
_SESSION_COOKIE_NAME = "adr_webui_session"
_SESSION_COOKIE_MANAGER_KEY = "adr_webui_session_cookie_manager"
_SESSION_TOKEN_VERSION = 1
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


def _urlsafe_b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _urlsafe_b64decode(value: str) -> Optional[bytes]:
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (TypeError, ValueError, binascii.Error):
        return None


def _session_signing_key(config: WebUIAuthConfig) -> bytes:
    """Derive a session-only signing key from the current password record.

    The password hash is already a high-entropy secret stored in the local
    configuration. Deriving the key from it avoids a second credential and
    makes every persistent browser session invalid as soon as the password is
    changed.
    """
    return hashlib.sha256(
        f"adr-webui-session-v{_SESSION_TOKEN_VERSION}:{config.password_hash}".encode(
            "utf-8"
        )
    ).digest()


def create_persistent_session_token(
    config: WebUIAuthConfig, *, now: Optional[float] = None
) -> str:
    """Create a signed, expiry-bound browser session token for one admin."""
    if not _configured(config):
        raise ValueError("Administrator account is not configured")
    issued_at = int(time.time() if now is None else now)
    payload = {
        "v": _SESSION_TOKEN_VERSION,
        "u": config.username,
        "iat": issued_at,
        "exp": issued_at + config.session_timeout_minutes * 60,
        "n": secrets.token_urlsafe(16),
    }
    encoded_payload = _urlsafe_b64encode(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    signature = hmac.new(
        _session_signing_key(config), encoded_payload.encode("ascii"), hashlib.sha256
    ).digest()
    return f"{encoded_payload}.{_urlsafe_b64encode(signature)}"


def verify_persistent_session_token(
    config: WebUIAuthConfig, token: object, *, now: Optional[float] = None
) -> bool:
    """Check a signed browser token without exposing its content to the UI."""
    if not _configured(config) or not isinstance(token, str):
        return False
    try:
        encoded_payload, encoded_signature = token.split(".", 1)
    except ValueError:
        return False
    raw_payload = _urlsafe_b64decode(encoded_payload)
    supplied_signature = _urlsafe_b64decode(encoded_signature)
    if raw_payload is None or supplied_signature is None:
        return False
    expected_signature = hmac.new(
        _session_signing_key(config), encoded_payload.encode("ascii"), hashlib.sha256
    ).digest()
    if not hmac.compare_digest(supplied_signature, expected_signature):
        return False
    try:
        payload = json.loads(raw_payload.decode("utf-8"))
        issued_at = int(payload["iat"])
        expires_at = int(payload["exp"])
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    current_time = int(time.time() if now is None else now)
    maximum_lifetime = config.session_timeout_minutes * 60
    return bool(
        payload.get("v") == _SESSION_TOKEN_VERSION
        and hmac.compare_digest(str(payload.get("u", "")), config.username)
        and issued_at <= current_time + 60
        and expires_at > current_time
        and 0 < expires_at - issued_at <= maximum_lifetime
    )


def _request_uses_https() -> bool:
    """Keep the cookie usable on LAN HTTP and secure behind HTTPS proxies."""
    try:
        headers = st.context.headers
    except Exception:
        return False
    forwarded_proto = str(headers.get("x-forwarded-proto", "")).split(",", 1)[0]
    if forwarded_proto.strip().lower() == "https":
        return True
    origin = str(headers.get("origin", "")).lower()
    return origin.startswith("https://")


def _session_cookie_value() -> str:
    """Read the cookie attached to this browser's WebSocket handshake."""
    try:
        value = st.context.cookies.get(_SESSION_COOKIE_NAME, "")
    except Exception:
        return ""
    return value if isinstance(value, str) else ""


def _cookie_manager() -> CookieManager:
    return CookieManager(key=_SESSION_COOKIE_MANAGER_KEY)


def _schedule_session_cookie(config: WebUIAuthConfig) -> None:
    """Queue a cookie write for the next authenticated Streamlit render.

    A form submission followed by ``st.rerun`` removes an immediately rendered
    component before its browser JavaScript can write the cookie. Keeping this
    small pending value in Streamlit session state lets the next stable render
    complete the browser-side write first.
    """
    now = time.time()
    st.session_state[_SESSION_PENDING_COOKIE] = {
        "token": create_persistent_session_token(config, now=now),
        "expires_at": now + config.session_timeout_minutes * 60,
    }
    st.session_state.pop(_SESSION_PENDING_COOKIE_CLEAR, None)


def _schedule_session_cookie_clear() -> None:
    """Queue removal so the cookie component stays mounted long enough to run."""
    st.session_state.pop(_SESSION_PENDING_COOKIE, None)
    st.session_state[_SESSION_PENDING_COOKIE_CLEAR] = True


def _flush_pending_session_cookie_operations() -> None:
    """Render one cookie operation after a rerun, then forget its server copy."""
    if st.session_state.pop(_SESSION_PENDING_COOKIE_CLEAR, False):
        manager = _cookie_manager()
        # CookieManager keeps a local cache populated asynchronously. Seed the
        # key so its delete helper can always issue the browser operation on
        # the first post-logout render.
        manager.cookies.setdefault(_SESSION_COOKIE_NAME, "")
        manager.delete(_SESSION_COOKIE_NAME, key="adr_webui_session_cookie_delete")
        return

    pending = st.session_state.pop(_SESSION_PENDING_COOKIE, None)
    if not isinstance(pending, Mapping):
        return
    token = pending.get("token")
    expires_at = pending.get("expires_at")
    if not isinstance(token, str) or not isinstance(expires_at, (int, float)):
        return
    max_age = max(1, int(expires_at - time.time()))
    _cookie_manager().set(
        _SESSION_COOKIE_NAME,
        token,
        key="adr_webui_session_cookie_set",
        path="/",
        expires_at=dt.datetime.fromtimestamp(expires_at, tz=dt.timezone.utc),
        max_age=max_age,
        secure=_request_uses_https(),
        same_site="strict",
    )


def _mark_authenticated(config: WebUIAuthConfig, *, persist: bool) -> None:
    st.session_state[_SESSION_AUTHENTICATED] = True
    st.session_state[_SESSION_LAST_ACTIVITY] = time.time()
    if persist:
        _schedule_session_cookie(config)


def _is_authenticated(config: WebUIAuthConfig) -> bool:
    now = time.time()
    if not st.session_state.get(_SESSION_AUTHENTICATED, False):
        if verify_persistent_session_token(config, _session_cookie_value(), now=now):
            _mark_authenticated(config, persist=False)
            return True
        return False
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


def _disabled_auth_values(env_values: Mapping[str, object]) -> dict[str, object]:
    """Return a safe env map for the explicit trusted-LAN opt-out."""
    updated = dict(env_values)
    updated.update(
        {
            "WEBUI_AUTH_ENABLED": "false",
            "WEBUI_ADMIN_USERNAME": "",
            "WEBUI_ADMIN_PASSWORD_HASH": "",
        }
    )
    return updated


def _disable_authentication(env_values: Mapping[str, object]) -> None:
    """Persist the explicit trusted-LAN opt-out from the first-run screen."""
    updated = _disabled_auth_values(env_values)
    _schedule_session_cookie_clear()
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
        st.caption(t("auth_skip_intranet_notice"))
        skip_authentication = st.form_submit_button(
            t("auth_skip_intranet"), type="secondary"
        )
    if skip_authentication:
        _disable_authentication(env_values)
        _clear_session(st.session_state)
        st.success(t("auth_skip_intranet_success"))
        st.rerun()
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
    _flush_pending_session_cookie_operations()
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
        _mark_authenticated(config, persist=True)
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
        _schedule_session_cookie_clear()
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
            _schedule_session_cookie_clear()
            _save_admin_account(env_values, config.username, new_password)
            _clear_session(st.session_state)
            st.success(t("auth_password_changed"))
