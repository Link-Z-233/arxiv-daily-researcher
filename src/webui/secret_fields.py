"""Small, auditable helpers for secret inputs in the local WebUI.

Password widgets only mask their contents visually.  Supplying a saved value
as ``value=`` would still send that value to the browser, so saved secrets are
never used to initialise a widget here.  A blank field deliberately means
"keep the persisted value"; clearing a secret requires an explicit checkbox.
"""

from collections.abc import Iterable, Mapping, MutableMapping
from typing import Any


_CLEAR_SUFFIX = "__clear_saved_secret"
_INITIALIZED_SUFFIX = "__secret_widget_initialized"


def secret_clear_key(field_key: str) -> str:
    """Return the session-state key for a secret field's clear control."""
    return f"{field_key}{_CLEAR_SUFFIX}"


def _initialized_key(field_key: str) -> str:
    return f"{field_key}{_INITIALIZED_SUFFIX}"


def initialize_secret_field_state(state: MutableMapping[str, Any], field_key: str) -> None:
    """Start a secret widget empty, including for sessions opened before this fix.

    The marker prevents later reruns from erasing a secret that the user has
    intentionally just entered.  Existing browser session state is reset once
    so a value previously populated from a saved ``.env`` cannot linger.
    """
    marker = _initialized_key(field_key)
    if marker not in state:
        state[field_key] = ""
        state[secret_clear_key(field_key)] = False
        state[marker] = True


def resolve_secret_value(
    env_values: Mapping[str, str],
    env_key: str,
    field_key: str,
    state: Mapping[str, Any],
) -> str:
    """Resolve a replacement, explicit deletion, or unchanged saved secret.

    A non-empty widget value wins over the clear checkbox.  This makes a
    pasted replacement safe even if a user had previously ticked "clear".
    """
    entered = state.get(field_key, "")
    if isinstance(entered, str) and entered != "":
        return entered
    if state.get(secret_clear_key(field_key), False):
        return ""
    saved = env_values.get(env_key, "")
    return saved if isinstance(saved, str) else str(saved or "")


def clear_secret_field_state(
    state: MutableMapping[str, Any], field_keys: Iterable[str]
) -> None:
    """Forget intentionally entered secrets after a successful configuration save."""
    for field_key in field_keys:
        state[field_key] = ""
        state[secret_clear_key(field_key)] = False


def render_secret_input(
    st: Any,
    *,
    label: str,
    env_values: Mapping[str, str],
    env_key: str,
    field_key: str,
    configured_hint: str,
    clear_label: str,
    help: str | None = None,
) -> str:
    """Render a blank password field without serialising a saved secret to UI."""
    initialize_secret_field_state(st.session_state, field_key)
    entered = st.text_input(
        label,
        value="",
        type="password",
        key=field_key,
        help=help,
    )
    if env_values.get(env_key):
        st.caption(configured_hint)
        st.checkbox(clear_label, key=secret_clear_key(field_key))
    return entered
