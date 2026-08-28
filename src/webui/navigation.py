"""Navigation structure shared by the Streamlit panel and its tests."""

from __future__ import annotations

from typing import Final

NavigationItem = tuple[str, str]
NavigationGroup = tuple[str, str, tuple[NavigationItem, ...]]


# The sidebar deliberately contains only these four workflow-level entries.
# Individual pages remain native top tabs in the selected group, preserving the
# familiar Streamlit tab navigation while keeping the sidebar compact.
NAVIGATION_GROUPS: Final[tuple[NavigationGroup, ...]] = (
    (
        "run",
        "nav_group_run",
        (
            ("daily_research", "nav_daily_research"),
            ("past_daily", "nav_past_daily"),
            ("trend_tasks", "nav_trend_tasks"),
            ("queue", "nav_queue"),
        ),
    ),
    (
        "content",
        "nav_group_content",
        (
            ("reports", "nav_reports"),
            ("favorites", "nav_favorites"),
            ("paper_search", "nav_paper_search"),
            ("analytics", "nav_analytics"),
        ),
    ),
    (
        "configuration",
        "nav_group_config",
        (
            ("keywords", "nav_keywords"),
            ("data_sources", "nav_data_sources"),
            ("scoring", "nav_scoring"),
            ("api", "nav_api"),
            ("notifications", "nav_notifications"),
            ("advanced", "nav_advanced"),
            ("accounts", "nav_accounts"),
        ),
    ),
    (
        "system",
        "nav_group_system",
        (
            ("backup_sync", "nav_backup_sync"),
            ("history_tasks", "nav_history_tasks"),
            ("diagnostics", "nav_diagnostics"),
            ("logs", "nav_logs"),
        ),
    ),
)

NAVIGATION_GROUP_IDS: Final[frozenset[str]] = frozenset(
    group_id for group_id, _label_key, _items in NAVIGATION_GROUPS
)
