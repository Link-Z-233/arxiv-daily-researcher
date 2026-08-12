"""Small helpers for rendering untrusted external URLs safely."""

from __future__ import annotations

import ipaddress
import socket
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


def _numeric_host_address(hostname: str):
    """Return a numeric IP hidden in a hostname spelling, if one is present.

    ``ipaddress`` intentionally rejects historical IPv4 spellings such as
    ``127.1`` and ``0x7f000001``.  Some HTTP stacks still interpret those as
    loopback addresses, so use ``inet_aton`` as a non-DNS fallback solely for
    detecting them before a background downloader connects.
    """
    try:
        return ipaddress.ip_address(hostname)
    except ValueError:
        pass
    try:
        return ipaddress.ip_address(socket.inet_aton(hostname))
    except (OSError, ValueError):
        return None


def safe_external_http_url(value: object) -> str:
    """Return a URL safe enough for an unattended external HTTP fetch.

    This is deliberately stricter than :func:`safe_http_url`, which is used
    only to render links.  It rejects embedded credentials and direct local or
    non-global numeric addresses so untrusted paper metadata cannot turn PDF
    parsing into a request to a local service.  Hostname DNS is not resolved
    here: resolving would break proxy deployments and cannot eliminate DNS
    rebinding by itself.  Callers must still validate every redirect and bound
    response size.
    """
    url = safe_http_url(value)
    if not url:
        return ""
    try:
        parsed = urlsplit(url)
        # Accessing port forces urlsplit to reject malformed ``host:port``
        # syntax as well as malformed bracketed IPv6 literals.
        _ = parsed.port
        hostname = parsed.hostname
    except ValueError:
        return ""
    if not hostname or parsed.username is not None or parsed.password is not None:
        return ""

    normalized_host = hostname.rstrip(".").casefold()
    if not normalized_host or normalized_host == "localhost" or normalized_host.endswith(
        ".localhost"
    ):
        return ""
    numeric_address = _numeric_host_address(normalized_host)
    if numeric_address is not None and not numeric_address.is_global:
        return ""
    return url
