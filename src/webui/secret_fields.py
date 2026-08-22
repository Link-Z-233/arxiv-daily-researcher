"""Small, auditable helpers for secret inputs in the local WebUI.

Password widgets only mask their contents visually.  Supplying a saved value
as ``value=`` would still send that value to the browser, so saved secrets are
never used to initialise a widget here.  A blank field deliberately means
"keep the persisted value"; to remove a secret, edit the .env file directly.
"""

from collections.abc import Iterable, Mapping, MutableMapping
from typing import Any


_INITIALIZED_SUFFIX = "__secret_widget_initialized"


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
        state[marker] = True


def resolve_secret_value(
    env_values: Mapping[str, str],
    env_key: str,
    field_key: str,
    state: Mapping[str, Any],
) -> str:
    """Resolve a replacement or the unchanged saved secret.

    A non-empty widget value replaces the saved secret; a blank field keeps
    whatever is already persisted.
    """
    entered = state.get(field_key, "")
    if isinstance(entered, str) and entered != "":
        return entered
    saved = env_values.get(env_key, "")
    return saved if isinstance(saved, str) else str(saved or "")


def clear_secret_field_state(
    state: MutableMapping[str, Any], field_keys: Iterable[str]
) -> None:
    """Forget intentionally entered secrets after a successful configuration save."""
    for field_key in field_keys:
        state[field_key] = ""


def render_secret_input(
    st: Any,
    *,
    label: str,
    env_values: Mapping[str, str],
    env_key: str,
    field_key: str,
    configured_hint: str,
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
    return entered
