"""Expose the modern WebUI's static browser translation catalogue."""

from __future__ import annotations

from functools import lru_cache
from typing import Mapping

from modern_webui.catalogue import _TRANSLATIONS


@lru_cache(maxsize=1)
def client_catalogue() -> dict[str, dict[str, str]]:
    """Return a validated copy suitable for a public pre-login endpoint."""

    result: dict[str, dict[str, str]] = {}
    for key, value in _TRANSLATIONS.items():
        if not isinstance(key, str) or not isinstance(value, Mapping):
            continue
        chinese = value.get("zh")
        english = value.get("en")
        if isinstance(chinese, str) and isinstance(english, str):
            result[key] = {"zh": chinese, "en": english}
    return result
