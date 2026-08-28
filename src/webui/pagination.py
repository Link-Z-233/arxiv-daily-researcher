"""Small, consistent pagination controls for multi-row Streamlit views.

The application has several independently rendered tabs.  Keeping page state
under an explicit caller-provided key prevents a table in one tab from
changing another tab's page or page-size selection.
"""

from __future__ import annotations

from typing import Any, Callable, Sequence, TypeVar

import streamlit as st

from webui.i18n import t


PAGE_SIZES = (5, 10)
Item = TypeVar("Item")


def page_window(total: int, page: int, page_size: int) -> tuple[int, int, int, int]:
    """Return safe ``start, end, page, pages`` values for one zero-based page."""
    safe_total = max(0, int(total))
    safe_size = page_size if page_size in PAGE_SIZES else PAGE_SIZES[0]
    pages = max(1, -(-safe_total // safe_size))
    safe_page = min(max(0, int(page)), pages - 1)
    return safe_page * safe_size, min(safe_total, (safe_page + 1) * safe_size), safe_page, pages


def _session_value(ui: Any, key: str, default: Any) -> Any:
    session = getattr(ui, "session_state", None)
    if session is None:
        return default
    try:
        return session.get(key, default)
    except AttributeError:
        try:
            return session[key] if key in session else default
        except (KeyError, TypeError):
            return default


def _set_session_value(ui: Any, key: str, value: Any) -> None:
    session = getattr(ui, "session_state", None)
    if session is None:
        return
    try:
        session[key] = value
    except (KeyError, TypeError):
        # Rendering must remain read-only and resilient in bare-mode tests.
        return


def _pagination_state(
    total: int, key: str, *, ui: Any, translate: Any
) -> tuple[int, int, int, int]:
    """Render the page-size selector and return its clamped page window."""
    if total <= PAGE_SIZES[0]:
        return page_window(total, 0, PAGE_SIZES[0])

    size_key = f"{key}_page_size"
    raw_size = _session_value(ui, size_key, PAGE_SIZES[0])
    default_index = PAGE_SIZES.index(raw_size) if raw_size in PAGE_SIZES else 0
    page_size = ui.selectbox(
        translate("pagination_page_size"),
        options=list(PAGE_SIZES),
        index=default_index,
        format_func=lambda size: translate("pagination_page_size_value").format(size=size),
        key=size_key,
    )
    if page_size not in PAGE_SIZES:
        page_size = PAGE_SIZES[0]

    page_key = f"{key}_page"
    raw_page = _session_value(ui, page_key, 0)
    try:
        requested_page = int(raw_page)
    except (TypeError, ValueError):
        requested_page = 0
    start, end, page, pages = page_window(total, requested_page, page_size)
    if page != requested_page:
        _set_session_value(ui, page_key, page)
    return start, end, page, pages


def _render_page_controls(
    *,
    total: int,
    start: int,
    end: int,
    page: int,
    pages: int,
    key: str,
    ui: Any,
    translate: Any,
) -> None:
    """Show compact previous/next controls below a paginated table."""
    if pages <= 1:
        return
    previous, info, following = ui.columns([1, 2, 1])
    page_key = f"{key}_page"
    if previous.button(
        translate("pagination_previous"),
        key=f"{key}_previous",
        disabled=page <= 0,
        width="stretch",
    ):
        _set_session_value(ui, page_key, page - 1)
    info.caption(
        translate("pagination_info").format(
            start=start + 1,
            end=end,
            total=total,
            page=page + 1,
            pages=pages,
        )
    )
    if following.button(
        translate("pagination_next"),
        key=f"{key}_next",
        disabled=page >= pages - 1,
        width="stretch",
    ):
        _set_session_value(ui, page_key, page + 1)


def render_paginated_dataframe(
    rows: Sequence[dict[str, Any]],
    *,
    key: str,
    ui: Any = None,
    translate: Any = None,
    **dataframe_kwargs: Any,
) -> tuple[int, int]:
    """Render rows in 5/10-row pages with an independent navigation state."""
    active_ui = ui or st
    active_translate = translate or t
    values = list(rows)
    start, end, page, pages = _pagination_state(
        len(values), key, ui=active_ui, translate=active_translate
    )
    active_ui.dataframe(values[start:end], **dataframe_kwargs)
    _render_page_controls(
        total=len(values),
        start=start,
        end=end,
        page=page,
        pages=pages,
        key=key,
        ui=active_ui,
        translate=active_translate,
    )
    return start, end


def render_paginated_table(
    table: Any, *, key: str, ui: Any = None, translate: Any = None
) -> tuple[int, int]:
    """Render a DataFrame-like table in 5/10-row pages without scroll frames."""
    active_ui = ui or st
    active_translate = translate or t
    total = len(table)
    start, end, page, pages = _pagination_state(
        total, key, ui=active_ui, translate=active_translate
    )
    if hasattr(table, "iloc"):
        visible = table.iloc[start:end]
    else:
        visible = table[start:end]
    active_ui.table(visible)
    _render_page_controls(
        total=total,
        start=start,
        end=end,
        page=page,
        pages=pages,
        key=key,
        ui=active_ui,
        translate=active_translate,
    )
    return start, end


def render_paginated_items(
    items: Sequence[Item],
    render_item: Callable[[Item], None],
    *,
    key: str,
    ui: Any = None,
    translate: Any = None,
) -> tuple[int, int]:
    """Render arbitrary repeated UI rows with the same 5/10-row controls."""
    active_ui = ui or st
    active_translate = translate or t
    values = list(items)
    start, end, page, pages = _pagination_state(
        len(values), key, ui=active_ui, translate=active_translate
    )
    for item in values[start:end]:
        render_item(item)
    _render_page_controls(
        total=len(values),
        start=start,
        end=end,
        page=page,
        pages=pages,
        key=key,
        ui=active_ui,
        translate=active_translate,
    )
    return start, end
