"""Context-aware helpers for rendering external data into Markdown.

Reports and notifications combine program-owned Markdown/HTML structure with
paper metadata and LLM output.  Escaping the latter at the boundary keeps a
title, abstract, or model response from becoming a heading, link, raw HTML
element, or platform-specific formatting directive.
"""

from __future__ import annotations

import html
import re
from urllib.parse import quote

from utils.safe_url import safe_http_url


# These characters can change Markdown/GFM structure.  ``<``, ``>``, ``&``
# and quotes are handled by ``html.escape`` after this pass, so HTML entities
# remain valid text entities rather than receiving a stray Markdown backslash.
_MARKDOWN_CONTROL_CHARACTERS = frozenset(r"\\*_{}[]()!|~")
_MARKDOWN_URL_SAFE_CHARACTERS = ":/?#[]@!$&'*+,;=%"
_LINE_PREFIX = re.compile(r"^(\s{0,3})([#>+\-=]|\d+\.)(?=\s|$)")
_BACKTICK_SENTINEL = "\x00markdown-backtick\x00"


class MarkdownFragment(str):
    """A Markdown fragment produced by this module, rather than external text."""


def markdown_text(value: object, *, multiline: bool = True) -> str:
    """Return external text that is safe to insert into Markdown structure.

    Program-generated Markdown should be composed separately around this
    value.  Newlines are retained for paragraphs, quotations and lists by
    default; table cells and link labels can request a single-line value.
    """
    if value is None:
        return ""
    if isinstance(value, MarkdownFragment):
        return str(value)

    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    if not multiline:
        text = " ".join(text.split("\n"))

    # Strip control characters which have no useful report representation but
    # can confuse downstream webhook/Markdown consumers.  Tabs and newlines
    # are retained when applicable for readable prose.
    text = "".join(
        character
        if character in {"\n", "\t"} or ord(character) >= 0x20
        else "\uFFFD"
        for character in text
    )
    # A backslash does not neutralize a backtick inside a Markdown code span.
    # Convert it to a character reference after HTML escaping instead, so the
    # parser never sees a literal delimiter while ordinary prose still displays
    # the original character.
    text = text.replace("`", _BACKTICK_SENTINEL)
    markdown_escaped = "".join(
        f"\\{character}" if character in _MARKDOWN_CONTROL_CHARACTERS else character
        for character in text
    )
    # These characters only introduce Markdown block syntax at the start of a
    # line.  Escaping them there avoids gratuitously changing normal prose such
    # as hyphenated paper titles, equations, decimal numbers, or identifiers.
    markdown_escaped = "\n".join(
        _LINE_PREFIX.sub(lambda match: f"{match.group(1)}\\{match.group(2)}", line)
        for line in markdown_escaped.split("\n")
    )
    return html.escape(markdown_escaped, quote=True).replace(_BACKTICK_SENTINEL, "&#96;")


def markdown_table_cell(value: object) -> str:
    """Return a safe one-line Markdown table cell."""
    return markdown_text(value, multiline=False)


def markdown_link(label: object, url: object) -> MarkdownFragment:
    """Return a safe Markdown link, or an empty string for an unsafe URL.

    URL percent-encoding excludes the Markdown-closing parenthesis and spaces,
    so a valid HTTP(S) URL cannot escape the destination portion of the link.
    """
    safe_url = safe_http_url(url)
    if not safe_url:
        return MarkdownFragment("")

    destination = quote(safe_url, safe=_MARKDOWN_URL_SAFE_CHARACTERS)
    return MarkdownFragment(f"[{markdown_text(label, multiline=False)}]({destination})")
