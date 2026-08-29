"""Read the shared panel translation catalogue without importing Streamlit.

The modern UI intentionally has a much smaller dependency set than the
compatibility panel.  The latter's ``webui.i18n`` module imports Streamlit for
session state, so importing it here would pull a presentation dependency into
the lightweight ASGI image.  Its translation dictionary is a static literal,
therefore parsing that literal gives both panels one source of wording while
keeping the modern image independent of Streamlit.
"""

from __future__ import annotations

import ast
from functools import lru_cache
from pathlib import Path


_CATALOGUE_PATH = Path(__file__).resolve().parents[1] / "webui" / "i18n.py"


@lru_cache(maxsize=1)
def client_catalogue() -> dict[str, dict[str, str]]:
    """Return safe Chinese/English strings used by the compatibility panel.

    A malformed or unavailable optional catalogue should not prevent a local
    panel from opening.  The frontend falls back to Chinese for any string
    missing from this response.
    """

    try:
        tree = ast.parse(_CATALOGUE_PATH.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return {}

    raw: object | None = None
    for node in tree.body:
        if not isinstance(node, ast.AnnAssign) or not isinstance(node.target, ast.Name):
            continue
        if node.target.id != "_TRANSLATIONS" or node.value is None:
            continue
        try:
            raw = ast.literal_eval(node.value)
        except (ValueError, TypeError):
            return {}
        break

    if not isinstance(raw, dict):
        return {}
    result: dict[str, dict[str, str]] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            continue
        chinese = value.get("zh")
        english = value.get("en")
        if isinstance(chinese, str) and isinstance(english, str):
            result[key] = {"zh": chinese, "en": english}
    return result
