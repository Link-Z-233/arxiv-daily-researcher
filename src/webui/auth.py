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
from webui.pagination import render_paginated_dataframe


_HASH_SCHEME = "pbkdf2_sha256"
_PBKDF2_ITERATIONS = 600_000
_MIN_PASSWORD_LENGTH = 6
_USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,63}$")
_SESSION_AUTHENTICATED = "_webui_authenticated"
_SESSION_USERNAME = "_webui_authenticated_username"
_SESSION_LAST_ACTIVITY = "_webui_auth_last_activity"
_SESSION_PENDING_COOKIE = "_webui_auth_pending_cookie"
_SESSION_PENDING_COOKIE_CLEAR = "_webui_auth_pending_cookie_clear"
_SESSION_COOKIE_NAME = "adr_webui_session"
_SESSION_COOKIE_MANAGER_KEY = "adr_webui_session_cookie_manager"
_SESSION_TOKEN_VERSION = 1
_ATTEMPT_WINDOW_SECONDS = 15 * 60
_ACCOUNTS_ENV_KEY = "WEBUI_ACCOUNTS"
_MAX_MANAGED_ACCOUNTS = 20
_attempt_lock = threading.Lock()
_attempt_state: dict[str, tuple[int, float, float]] = {}


@dataclass(frozen=True)
class WebUIAccount:
    """One full-access local WebUI account without ever exposing its hash."""

    username: str
    password_hash: str
    is_owner: bool = False


@dataclass(frozen=True)
class WebUIAuthConfig:
    """Normalized auth configuration read from the `.env` map."""

    enabled: bool
    username: str
    password_hash: str
    session_timeout_minutes: int
    accounts: tuple[WebUIAccount, ...] = ()


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
    accounts = _read_managed_accounts(env_values.get(_ACCOUNTS_ENV_KEY))
    if accounts:
        primary = next((account for account in accounts if account.is_owner), accounts[0])
        username = primary.username
        password_hash = primary.password_hash
    else:
        username = str(env_values.get("WEBUI_ADMIN_USERNAME", "")).strip()
        password_hash = str(env_values.get("WEBUI_ADMIN_PASSWORD_HASH", "")).strip()
        if _USERNAME_PATTERN.fullmatch(username) and password_hash:
            accounts = (WebUIAccount(username, password_hash, is_owner=True),)
    return WebUIAuthConfig(
        enabled=_as_bool(env_values.get("WEBUI_AUTH_ENABLED"), True),
        username=username,
        password_hash=password_hash,
        session_timeout_minutes=_bounded_int(
            env_values.get("WEBUI_SESSION_TIMEOUT_MINUTES"),
            10_080,
            minimum=5,
            maximum=10_080,
        ),
        accounts=accounts,
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


def _read_managed_accounts(raw_value: object) -> tuple[WebUIAccount, ...]:
    """Decode the optional compact account registry stored in ``.env``.

    The registry is base64url JSON so it remains one dotenv-safe line.  A
    malformed registry deliberately falls back to the legacy owner variables;
    this keeps a hand-edited or partially restored file from locking out the
    original administrator.
    """
    if not isinstance(raw_value, str) or not raw_value.strip():
        return ()
    encoded = raw_value.strip()
    if len(encoded) > 32_768:
        return ()
    decoded = _urlsafe_b64decode(encoded)
    if decoded is None:
        return ()
    try:
        payload = json.loads(decoded.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        return ()
    if not isinstance(payload, dict) or payload.get("v") != 1:
        return ()
    raw_accounts = payload.get("accounts")
    if not isinstance(raw_accounts, list) or not raw_accounts:
        return ()

    accounts: list[WebUIAccount] = []
    usernames: set[str] = set()
    owner_seen = False
    for item in raw_accounts[:_MAX_MANAGED_ACCOUNTS]:
        if not isinstance(item, dict):
            return ()
        username = str(item.get("u") or "").strip()
        password_hash = str(item.get("p") or "").strip()
        if (
            not _USERNAME_PATTERN.fullmatch(username)
            or username in usernames
            or verify_password_hash(password_hash, "") is None
        ):
            return ()
        is_owner = bool(item.get("o")) and not owner_seen
        owner_seen = owner_seen or is_owner
        accounts.append(WebUIAccount(username, password_hash, is_owner=is_owner))
        usernames.add(username)

    if not accounts:
        return ()
    if not owner_seen:
        first = accounts[0]
        accounts[0] = WebUIAccount(first.username, first.password_hash, is_owner=True)
    return tuple(accounts)


def _serialize_managed_accounts(accounts: tuple[WebUIAccount, ...]) -> str:
    """Encode a validated account registry for a single dotenv value."""
    payload = {
        "v": 1,
        "accounts": [
            {"u": account.username, "p": account.password_hash, "o": account.is_owner}
            for account in accounts
        ],
    }
    return _urlsafe_b64encode(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )


def accounts_for_config(config: WebUIAuthConfig) -> tuple[WebUIAccount, ...]:
    """Return valid accounts, including a compatibility view of old settings."""
    if config.accounts:
        return config.accounts
    if (
        _USERNAME_PATTERN.fullmatch(config.username)
        and verify_password_hash(config.password_hash, "") is not None
    ):
        return (WebUIAccount(config.username, config.password_hash, is_owner=True),)
    return ()


def find_account(config: WebUIAuthConfig, username: object) -> Optional[WebUIAccount]:
    """Find one account by its exact, validated username."""
    candidate = str(username or "").strip()
    for account in accounts_for_config(config):
        if hmac.compare_digest(account.username, candidate):
            return account
    return None


def _configured(config: WebUIAuthConfig) -> bool:
    return bool(accounts_for_config(config))


def _clear_session(session: MutableMapping[str, object]) -> None:
    for key in (
        _SESSION_AUTHENTICATED,
        _SESSION_USERNAME,
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


def _session_signing_key(password_hash: str) -> bytes:
    """Derive a session-only signing key from one account password record.

    The password hash is already a high-entropy secret stored in the local
    configuration. Deriving the key from it avoids a second credential and
    makes every persistent browser session invalid as soon as the password is
    changed.
    """
    return hashlib.sha256(
        f"adr-webui-session-v{_SESSION_TOKEN_VERSION}:{password_hash}".encode(
            "utf-8"
        )
    ).digest()


def create_persistent_session_token(
    config: WebUIAuthConfig,
    *,
    username: Optional[str] = None,
    now: Optional[float] = None,
) -> str:
    """Create a signed, expiry-bound browser session token for one account."""
    account = find_account(config, username or config.username)
    if account is None:
        raise ValueError("WebUI account is not configured")
    issued_at = int(time.time() if now is None else now)
    payload = {
        "v": _SESSION_TOKEN_VERSION,
        "u": account.username,
        "iat": issued_at,
        "exp": issued_at + config.session_timeout_minutes * 60,
        "n": secrets.token_urlsafe(16),
    }
    encoded_payload = _urlsafe_b64encode(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    signature = hmac.new(
        _session_signing_key(account.password_hash),
        encoded_payload.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{encoded_payload}.{_urlsafe_b64encode(signature)}"


def persistent_session_username(
    config: WebUIAuthConfig, token: object, *, now: Optional[float] = None
) -> Optional[str]:
    """Return the token account when a signed browser session remains valid."""
    if not _configured(config) or not isinstance(token, str):
        return None
    try:
        encoded_payload, encoded_signature = token.split(".", 1)
    except ValueError:
        return None
    raw_payload = _urlsafe_b64decode(encoded_payload)
    supplied_signature = _urlsafe_b64decode(encoded_signature)
    if raw_payload is None or supplied_signature is None:
        return None
    try:
        payload = json.loads(raw_payload.decode("utf-8"))
        issued_at = int(payload["iat"])
        expires_at = int(payload["exp"])
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    username = payload.get("u")
    account = find_account(config, username)
    if account is None:
        return None
    expected_signature = hmac.new(
        _session_signing_key(account.password_hash),
        encoded_payload.encode("ascii"),
        hashlib.sha256,
    ).digest()
    if not hmac.compare_digest(supplied_signature, expected_signature):
        return None
    current_time = int(time.time() if now is None else now)
    maximum_lifetime = config.session_timeout_minutes * 60
    if not (
        payload.get("v") == _SESSION_TOKEN_VERSION
        and hmac.compare_digest(str(username or ""), account.username)
        and issued_at <= current_time + 60
        and expires_at > current_time
        and 0 < expires_at - issued_at <= maximum_lifetime
    ):
        return None
    return account.username


def verify_persistent_session_token(
    config: WebUIAuthConfig, token: object, *, now: Optional[float] = None
) -> bool:
    """Check a signed browser token without exposing its account to callers."""
    return persistent_session_username(config, token, now=now) is not None


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
    username = st.session_state.get(_SESSION_USERNAME)
    if not isinstance(username, str) or find_account(config, username) is None:
        username = config.username
    st.session_state[_SESSION_PENDING_COOKIE] = {
        "token": create_persistent_session_token(config, username=username, now=now),
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


def _mark_authenticated(
    config: WebUIAuthConfig, username: str, *, persist: bool
) -> None:
    st.session_state[_SESSION_AUTHENTICATED] = True
    st.session_state[_SESSION_USERNAME] = username
    st.session_state[_SESSION_LAST_ACTIVITY] = time.time()
    if persist:
        _schedule_session_cookie(config)


def _is_authenticated(config: WebUIAuthConfig) -> bool:
    now = time.time()
    if not st.session_state.get(_SESSION_AUTHENTICATED, False):
        username = persistent_session_username(
            config, _session_cookie_value(), now=now
        )
        if username:
            _mark_authenticated(config, username, persist=False)
            return True
        return False
    username = st.session_state.get(_SESSION_USERNAME)
    if not isinstance(username, str) or find_account(config, username) is None:
        _clear_session(st.session_state)
        return False
    last_activity = st.session_state.get(_SESSION_LAST_ACTIVITY)
    if not isinstance(last_activity, (int, float)) or (
        now - last_activity > config.session_timeout_minutes * 60
    ):
        _clear_session(st.session_state)
        return False
    st.session_state[_SESSION_LAST_ACTIVITY] = now
    return True


def current_authenticated_username(config: WebUIAuthConfig) -> Optional[str]:
    """Return the signed-in account name without trusting stale session data."""
    username = st.session_state.get(_SESSION_USERNAME)
    if isinstance(username, str) and find_account(config, username) is not None:
        return username
    return None


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


def _persist_managed_accounts(
    env_values: Mapping[str, object], accounts: tuple[WebUIAccount, ...]
) -> None:
    """Persist a bounded account registry and retain a legacy owner fallback."""
    if not accounts or len(accounts) > _MAX_MANAGED_ACCOUNTS:
        raise ValueError("auth_account_invalid_registry")
    owners = [account for account in accounts if account.is_owner]
    if len(owners) != 1:
        raise ValueError("auth_account_invalid_registry")
    usernames: set[str] = set()
    for account in accounts:
        if (
            not _USERNAME_PATTERN.fullmatch(account.username)
            or account.username in usernames
            or verify_password_hash(account.password_hash, "") is None
        ):
            raise ValueError("auth_account_invalid_registry")
        usernames.add(account.username)

    owner = owners[0]
    updated = dict(env_values)
    updated.update(
        {
            "WEBUI_AUTH_ENABLED": "true",
            # Keep these values as a recoverable, backward-compatible owner
            # fallback. Newer deployments read the complete registry first.
            "WEBUI_ADMIN_USERNAME": owner.username,
            "WEBUI_ADMIN_PASSWORD_HASH": owner.password_hash,
            _ACCOUNTS_ENV_KEY: _serialize_managed_accounts(accounts),
        }
    )
    write_env(updated)
    st.cache_data.clear()


def _save_admin_account(env_values: Mapping[str, object], username: str, password: str) -> None:
    """Create the first owner account during initial setup."""
    _persist_managed_accounts(
        env_values,
        (WebUIAccount(username, hash_password(password), is_owner=True),),
    )


def _require_owner(config: WebUIAuthConfig, actor_username: str) -> WebUIAccount:
    actor = find_account(config, actor_username)
    if actor is None:
        raise ValueError("auth_account_not_found")
    if not actor.is_owner:
        raise ValueError("auth_account_owner_required")
    return actor


def create_managed_account(
    env_values: Mapping[str, object],
    *,
    actor_username: str,
    username: str,
    password: str,
) -> None:
    """Add one full-access account; only the owner may manage the registry."""
    config = read_auth_config(env_values)
    _require_owner(config, actor_username)
    username = username.strip()
    if validation_error := validate_username(username):
        raise ValueError(validation_error)
    if validation_error := validate_password(password):
        raise ValueError(validation_error)
    accounts = accounts_for_config(config)
    if find_account(config, username) is not None:
        raise ValueError("auth_account_exists")
    if len(accounts) >= _MAX_MANAGED_ACCOUNTS:
        raise ValueError("auth_account_limit")
    _persist_managed_accounts(
        env_values,
        (*accounts, WebUIAccount(username, hash_password(password))),
    )


def change_own_password(
    env_values: Mapping[str, object],
    *,
    username: str,
    current_password: str,
    new_password: str,
) -> None:
    """Change the signed-in account's password after verifying its old one."""
    config = read_auth_config(env_values)
    account = find_account(config, username)
    if account is None:
        raise ValueError("auth_account_not_found")
    if verify_password_hash(account.password_hash, current_password) is not True:
        raise ValueError("auth_current_password_invalid")
    if validation_error := validate_password(new_password):
        raise ValueError(validation_error)
    updated_accounts = tuple(
        WebUIAccount(
            item.username,
            hash_password(new_password) if item.username == account.username else item.password_hash,
            is_owner=item.is_owner,
        )
        for item in accounts_for_config(config)
    )
    _persist_managed_accounts(env_values, updated_accounts)


def reset_managed_account_password(
    env_values: Mapping[str, object],
    *,
    actor_username: str,
    target_username: str,
    new_password: str,
) -> None:
    """Allow the owner to reset a secondary account without its old password."""
    config = read_auth_config(env_values)
    _require_owner(config, actor_username)
    target = find_account(config, target_username)
    if target is None:
        raise ValueError("auth_account_not_found")
    if target.is_owner:
        raise ValueError("auth_account_reset_owner_via_self")
    if validation_error := validate_password(new_password):
        raise ValueError(validation_error)
    updated_accounts = tuple(
        WebUIAccount(
            item.username,
            hash_password(new_password) if item.username == target.username else item.password_hash,
            is_owner=item.is_owner,
        )
        for item in accounts_for_config(config)
    )
    _persist_managed_accounts(env_values, updated_accounts)


def delete_managed_account(
    env_values: Mapping[str, object],
    *,
    actor_username: str,
    target_username: str,
) -> None:
    """Delete a secondary account while keeping the original owner recoverable."""
    config = read_auth_config(env_values)
    _require_owner(config, actor_username)
    target = find_account(config, target_username)
    if target is None:
        raise ValueError("auth_account_not_found")
    if target.is_owner:
        raise ValueError("auth_account_cannot_delete_owner")
    _persist_managed_accounts(
        env_values,
        tuple(
            account
            for account in accounts_for_config(config)
            if account.username != target.username
        ),
    )


def _disabled_auth_values(env_values: Mapping[str, object]) -> dict[str, object]:
    """Return a safe env map for the explicit trusted-LAN opt-out."""
    updated = dict(env_values)
    updated.update(
        {
            "WEBUI_AUTH_ENABLED": "false",
            "WEBUI_ADMIN_USERNAME": "",
            "WEBUI_ADMIN_PASSWORD_HASH": "",
            _ACCOUNTS_ENV_KEY: "",
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

    normalized_username = username.strip()
    account = find_account(config, normalized_username)
    password_ok = (
        verify_password_hash(account.password_hash, password)
        if account is not None
        else False
    )
    if account is not None and password_ok is True:
        _mark_authenticated(config, account.username, persist=True)
        _clear_attempts(account.username)
        st.session_state.pop("webui_auth_password", None)
        st.rerun()

    _record_failed_attempt(normalized_username or config.username)
    st.session_state.pop("webui_auth_password", None)
    st.error(t("auth_login_failed"))
    return False


def render_account_controls(env_values: Mapping[str, object]) -> None:
    """Render only session controls in the sidebar; accounts live in Config."""
    config = read_auth_config(env_values)
    if not config.enabled:
        return
    if st.button(t("auth_logout"), key="webui_auth_logout", width="stretch"):
        _schedule_session_cookie_clear()
        _clear_session(st.session_state)
        st.rerun()


def _render_account_error(error: ValueError) -> None:
    """Present only intentional account-management validation messages."""
    key = str(error)
    st.error(t(key) if key.startswith("auth_") else t("auth_account_invalid_registry"))


def render_account_management(env_values: Mapping[str, object]) -> None:
    """Render full-access account administration in the Configuration group."""
    config = read_auth_config(env_values)
    st.caption(t("auth_accounts_hint"))
    if not config.enabled:
        st.info(t("auth_accounts_disabled"))
        return

    username = current_authenticated_username(config)
    if username is None:
        # The page is always mounted behind require_authentication. Keep this
        # guard for callers embedding the renderer in another page.
        st.error(t("auth_login_failed"))
        return
    actor = find_account(config, username)
    if actor is None:
        st.error(t("auth_account_not_found"))
        return

    st.caption(f"**{t('auth_account_list_title')}**")
    account_rows = [
        {
            t("auth_account_col_username"): account.username,
            t("auth_account_col_role"): t(
                "auth_account_role_owner"
                if account.is_owner
                else "auth_account_role_admin"
            ),
            t("auth_account_col_current"): (
                t("auth_account_current")
                if account.username == actor.username
                else "—"
            ),
        }
        for account in accounts_for_config(config)
    ]
    render_paginated_dataframe(
        account_rows,
        key="account_management_accounts",
        ui=st,
        translate=t,
        hide_index=True,
        width="stretch",
    )

    st.divider()
    st.caption(f"**{t('auth_change_own_password')}**")
    with st.form("webui_account_change_own_password", clear_on_submit=True):
        current_password = st.text_input(
            t("auth_current_password"), type="password", key="webui_account_current_password"
        )
        new_password = st.text_input(
            t("auth_new_password"), type="password", key="webui_account_new_password"
        )
        new_password_again = st.text_input(
            t("auth_password_confirm"),
            type="password",
            key="webui_account_new_password_confirm",
        )
        change_own = st.form_submit_button(t("auth_save_password"), type="primary")
    if change_own:
        if new_password != new_password_again:
            st.error(t("auth_password_mismatch"))
        else:
            try:
                change_own_password(
                    env_values,
                    username=actor.username,
                    current_password=current_password,
                    new_password=new_password,
                )
            except ValueError as exc:
                _render_account_error(exc)
            else:
                _schedule_session_cookie_clear()
                _clear_session(st.session_state)
                st.success(t("auth_password_changed"))

    if not actor.is_owner:
        return

    st.divider()
    st.caption(f"**{t('auth_account_add_title')}**")
    with st.form("webui_account_add", clear_on_submit=True):
        new_username = st.text_input(t("auth_username"), key="webui_account_add_username")
        new_account_password = st.text_input(
            t("auth_password"), type="password", key="webui_account_add_password"
        )
        new_account_password_again = st.text_input(
            t("auth_password_confirm"),
            type="password",
            key="webui_account_add_password_confirm",
        )
        add_account = st.form_submit_button(t("auth_account_add"), type="primary")
    if add_account:
        if new_account_password != new_account_password_again:
            st.error(t("auth_password_mismatch"))
        else:
            try:
                create_managed_account(
                    env_values,
                    actor_username=actor.username,
                    username=new_username,
                    password=new_account_password,
                )
            except ValueError as exc:
                _render_account_error(exc)
            else:
                st.toast(t("auth_account_added"), icon="👤")
                st.rerun()

    secondary_accounts = [
        account.username for account in accounts_for_config(config) if not account.is_owner
    ]
    if not secondary_accounts:
        st.caption(t("auth_account_none_secondary"))
        return

    st.divider()
    st.caption(f"**{t('auth_account_reset_title')}**")
    with st.form("webui_account_reset_password", clear_on_submit=True):
        reset_target = st.selectbox(
            t("auth_account_target"), secondary_accounts, key="webui_account_reset_target"
        )
        reset_password = st.text_input(
            t("auth_new_password"), type="password", key="webui_account_reset_password"
        )
        reset_password_again = st.text_input(
            t("auth_password_confirm"),
            type="password",
            key="webui_account_reset_password_confirm",
        )
        reset_account = st.form_submit_button(t("auth_account_reset"))
    if reset_account:
        if reset_password != reset_password_again:
            st.error(t("auth_password_mismatch"))
        else:
            try:
                reset_managed_account_password(
                    env_values,
                    actor_username=actor.username,
                    target_username=reset_target,
                    new_password=reset_password,
                )
            except ValueError as exc:
                _render_account_error(exc)
            else:
                st.toast(t("auth_account_reset_done"), icon="🔑")
                st.rerun()

    st.divider()
    st.caption(f"**{t('auth_account_remove_title')}**")
    with st.form("webui_account_remove", clear_on_submit=True):
        remove_target = st.selectbox(
            t("auth_account_target"), secondary_accounts, key="webui_account_remove_target"
        )
        remove_confirmed = st.checkbox(
            t("auth_account_remove_confirm"), key="webui_account_remove_confirm"
        )
        remove_account = st.form_submit_button(t("auth_account_remove"))
    if remove_account:
        if not remove_confirmed:
            st.warning(t("auth_account_remove_confirm"))
        else:
            try:
                delete_managed_account(
                    env_values,
                    actor_username=actor.username,
                    target_username=remove_target,
                )
            except ValueError as exc:
                _render_account_error(exc)
            else:
                st.toast(t("auth_account_removed"), icon="🗑️")
                st.rerun()
