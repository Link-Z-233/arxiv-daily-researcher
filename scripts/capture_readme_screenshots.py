#!/usr/bin/env python3
"""Capture current, privacy-safe WebUI screenshots for the project README.

Only pages that contain no credentials or private endpoints are captured.  The
script intentionally avoids API, notifications, and the top of data-management
where connection fields can appear.  Review the generated files before a
release if a local configuration has customized any labels.
"""

from __future__ import annotations

import re
import os
import sys
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright


BASE_URL = "http://127.0.0.1:8503"
OUTPUT_DIR = Path(__file__).resolve().parents[1] / "assets"
VIEWPORT = {"width": 1440, "height": 980}


def wait_for_streamlit(page: Page, timeout_seconds: float = 30) -> None:
    """Wait for the app shell and pending reruns to settle."""
    page.wait_for_load_state("domcontentloaded")
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            if "Running" not in page.locator("body").inner_text(timeout=1000):
                break
        except Exception:
            pass
        time.sleep(0.25)
    page.wait_for_timeout(900)


def authenticate(page: Page) -> None:
    """Sign in when the privacy-safe capture target enables WebUI auth.

    Credentials are deliberately read only from the process environment; the
    script never prints them or puts them in a screenshot. A fresh deployment
    must be initialized locally before automated documentation capture runs.
    """
    if page.get_by_text(re.compile("初始化管理员账户|Set Up the Administrator Account")).count():
        raise RuntimeError(
            "WebUI administrator account is not initialized. Complete local setup first."
        )
    if not page.get_by_text(re.compile("登录配置面板|Sign in to the configuration panel")).count():
        return
    username = os.environ.get("ADR_SCREENSHOT_USERNAME", "")
    password = os.environ.get("ADR_SCREENSHOT_PASSWORD", "")
    if not username or not password:
        raise RuntimeError(
            "WebUI authentication is enabled; set ADR_SCREENSHOT_USERNAME and "
            "ADR_SCREENSHOT_PASSWORD for screenshot capture."
        )
    page.get_by_label(re.compile("^(用户名|Username)$")).fill(username)
    page.get_by_label(re.compile("^(密码|Password)$")).fill(password)
    page.get_by_role("button", name=re.compile("^(登录|Sign in)$")).click()
    page.get_by_role("tab", name=re.compile("^(每日推送|Daily Push)$")).wait_for(
        timeout=30_000
    )
    wait_for_streamlit(page)


def show_tab(page: Page, label: str) -> None:
    page.get_by_role("tab", name=re.compile(f"^{re.escape(label)}$")).click()
    wait_for_streamlit(page)
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(250)


def capture(page: Page, filename: str) -> None:
    page.screenshot(path=str(OUTPUT_DIR / filename), full_page=False)
    print(f"captured {filename}")


def hide_sidebar(page: Page) -> None:
    page.locator('section[data-testid="stSidebar"]').evaluate(
        "node => node.style.display = 'none'"
    )


def scroll_to_text(page: Page, label: str) -> None:
    target = page.get_by_text(re.compile(re.escape(label))).first
    target.scroll_into_view_if_needed(timeout=10_000)
    target.evaluate("node => node.scrollIntoView({block: 'start', inline: 'nearest'})")
    page.wait_for_timeout(500)


def isolate_section(page: Page, label: str, keep_following: int) -> None:
    """Temporarily hide unrelated Streamlit elements for a focused image."""
    target = page.get_by_text(re.compile(re.escape(label))).first
    target.evaluate(
        """(node, keepFollowing) => {
            const heading = node.closest('.stElementContainer');
            const parent = heading && heading.parentElement;
            if (!heading || !parent) return;
            const children = Array.from(parent.children);
            const index = children.indexOf(heading);
            children.forEach((child, childIndex) => {
                if (childIndex < index || childIndex > index + keepFollowing) {
                    child.style.display = 'none';
                }
            });
        }""",
        keep_following,
    )
    page.wait_for_timeout(300)


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport=VIEWPORT, device_scale_factor=1)
        page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30_000)
        wait_for_streamlit(page, timeout_seconds=60)
        authenticate(page)

        # Keeping the main content wide makes README images legible while
        # avoiding the sidebar's runtime controls and local-version text.
        hide_sidebar(page)

        show_tab(page, "每日推送")
        capture(page, "webui_daily_push_v4.png")

        show_tab(page, "数据分析")
        scroll_to_text(page, "LLM 健康")
        capture(page, "webui_analytics_v4.png")

        show_tab(page, "评分")
        capture(page, "webui_scoring_v4.png")

        show_tab(page, "数据管理")
        isolate_section(page, "数据库备份", keep_following=4)
        scroll_to_text(page, "数据库备份")
        page.set_viewport_size({"width": VIEWPORT["width"], "height": 540})
        capture(page, "webui_data_management_v4.png")

        page.set_viewport_size(VIEWPORT)
        page.reload(wait_until="domcontentloaded", timeout=30_000)
        wait_for_streamlit(page)
        hide_sidebar(page)
        show_tab(page, "数据管理")
        isolate_section(page, "旧版本历史导入", keep_following=2)
        scroll_to_text(page, "旧版本历史导入")
        page.set_viewport_size({"width": VIEWPORT["width"], "height": 360})
        capture(page, "webui_history_import_v4.png")

        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
