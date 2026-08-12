"""Small helpers for rendering untrusted external URLs safely."""

from __future__ import annotations

from urllib.parse import urlsplit


def safe_http_url(value: object) -> str:
    """Return a normalized HTTP(S) URL, or an empty string when unsafe.

    Paper metadata and LLM-derived content are external input.  HTML escaping
    an attribute prevents quote injection but does not make a ``javascript:``
    or ``data:`` URL safe to follow, so report and notification renderers must
    validate the scheme before creating a link.
    """
    if value is None:
        return ""

    url = str(value).strip()
    if not url or any(ord(character) < 0x20 for character in url):
        return ""

    try:
        parsed = urlsplit(url)
    except ValueError:
        return ""

    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return ""
    return url
