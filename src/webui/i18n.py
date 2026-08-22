"""
Internationalization (i18n) support for the Streamlit config panel.

Provides a t() function that returns the translated string based on the
current language stored in st.session_state["lang"] (default: "zh").
"""

import streamlit as st

# ─── Translation dictionary ────────────────────────────────────────────────
_TRANSLATIONS: dict[str, dict[str, str]] = {
    # ── config_panel.py ──────────────────────────────────────────────────
    "sidebar_caption": {
        "zh": "配置面板",
        "en": "Configuration Panel",
    },
    "save_btn": {
        "zh": "保存所有更改",
        "en": "Save All Changes",
    },
    "reload_btn": {
        "zh": "从磁盘重新加载",
        "en": "Reload from Disk",
    },
    "save_success": {
        "zh": "配置已保存！",
        "en": "Configuration saved!",
    },
    "save_failed": {
        "zh": "保存失败: ",
        "en": "Save failed: ",
    },
    "file_found": {
        "zh": "已找到",
        "en": "Found",
    },
    "file_not_found": {
        "zh": "未找到",
        "en": "Not found",
    },
    "lang_toggle": {
        "zh": "English",
        "en": "中文",
    },
    # Tab labels
    "tab_llm": {"zh": "API", "en": "API"},
    "tab_search": {"zh": "搜索与数据源", "en": "Search & Sources"},
    "tab_keywords": {"zh": "关键词", "en": "Keywords"},
    "tab_scoring": {"zh": "评分", "en": "Scoring"},
    "tab_notifications": {"zh": "通知", "en": "Notifications"},
    "tab_advanced": {"zh": "高级设置", "en": "Advanced"},
    "tab_reports": {"zh": "报告查看", "en": "Reports"},
    "tab_run_manager": {"zh": "每日推送", "en": "Daily Push"},
    "tab_trend_runner": {"zh": "趋势分析", "en": "Trend Analysis"},
    "rm_auto_refresh": {"zh": "自动刷新", "en": "Auto refresh"},
    "rm_auto_refresh_help": {
        "zh": "开启后运行状态与日志尾部每 5 秒自动刷新，无需手动刷新页面。",
        "en": "When on, run status and the log tail refresh every 5 seconds.",
    },
    "rm_live_tail_hint": {
        "zh": "尾部 12 行 · 每 5 秒自动刷新",
        "en": "last 12 lines · auto-refreshes every 5s",
    },
    "rm_queue_pending": {"zh": "待处理队列", "en": "Pending queue"},
    "rm_queue_failed": {"zh": "失败待重试", "en": "Awaiting retry"},
    "rm_stop_btn": {"zh": "停止运行", "en": "Stop run"},
    "rm_trigger_state_failed": {
        "zh": "上次请求 {state}{suffix}；详情见运行日志。",
        "en": "Last request {state}{suffix}; see run logs.",
    },
    "rm_trigger_docker_keep": {
        "zh": "为避免丢失主容器尚未消费的请求，Docker 模式不从 WebUI 删除队列文件。",
        "en": "In Docker mode the WebUI does not delete queue files the worker may not have consumed yet.",
    },
    "rm_log_truncated": {
        "zh": "... (省略前 {skipped} 行，仅显示最后 {kept} 行) ...",
        "en": "... ({skipped} earlier lines skipped, last {kept} shown) ...",
    },
    "rm_log_read_failed": {"zh": "读取日志失败", "en": "Failed to read log"},
    "report_show_non_arxiv_help": {
        "zh": "开启后显示所有来源；关闭后仅显示 ArXiv 来源的每日研究报告",
        "en": "Show every source when on; only ArXiv daily reports when off",
    },
    "rm_stop_confirm": {"zh": "确认停止", "en": "Confirm stop"},
    "rm_stop_confirm_hint": {
        "zh": "将向正在运行的进程发送停止信号：已完成阶段保留，未完成论文留队待重试。"
              "仅对 WebUI 触发的运行生效。",
        "en": "Sends a stop signal to the running process: finished stages are "
              "kept and unfinished papers stay queued. WebUI-triggered runs only.",
    },
    "rm_stop_sent": {
        "zh": "停止请求已发送（PID {pids}），几秒内生效。",
        "en": "Stop requested (PID {pids}); takes effect within seconds.",
    },
    "rm_stop_failed": {"zh": "停止请求写入失败: {err}", "en": "Failed to write stop request: {err}"},
    "rm_stop_no_pid": {
        "zh": "未能从锁文件解析出 PID，无法发送停止请求。",
        "en": "No PID parsed from the lock file; cannot request a stop.",
    },
    "rm_status_skipped_busy": {
        "zh": "上次请求未执行：已有相同任务在运行（被运行锁跳过）。",
        "en": "Last request did not run: an identical task was already active.",
    },
    # ── usage.py ────────────────────────────────────────────────────────
    "usage_title": {"zh": "LLM Token 用量", "en": "LLM Token Usage"},
    "usage_empty": {
        "zh": "暂无用量数据——完成一次每日研究或趋势分析后，这里会出现统计。",
        "en": "No usage yet — complete a daily or trend run and stats appear here.",
    },
    "usage_load_failed": {
        "zh": "用量数据读取失败",
        "en": "Failed to load usage data",
    },
    "usage_heatmap_title": {
        "zh": "每日用量热力图（近一年）",
        "en": "Daily usage heatmap (last year)",
    },
    "usage_heatmap_less": {"zh": "少", "en": "Less"},
    "usage_heatmap_more": {"zh": "多", "en": "More"},
    "usage_trend_title": {"zh": "用量趋势", "en": "Usage trend"},
    "usage_range_label": {"zh": "时间段", "en": "Time range"},
    "usage_range_7": {"zh": "近7天", "en": "7 days"},
    "usage_range_30": {"zh": "近30天", "en": "30 days"},
    "usage_range_90": {"zh": "近90天", "en": "90 days"},
    "usage_range_365": {"zh": "近365天", "en": "365 days"},
    "usage_range_all": {"zh": "全部", "en": "All"},
    "usage_prompt_tokens": {"zh": "输入 tokens", "en": "Prompt tokens"},
    "usage_completion_tokens": {"zh": "输出 tokens", "en": "Completion tokens"},
    "usage_total_tokens": {"zh": "总 tokens", "en": "Total tokens"},
    "usage_by_model_title": {"zh": "按模型汇总", "en": "By model"},
    "usage_model": {"zh": "模型", "en": "Model"},
    "usage_today_prompt": {"zh": "当日输入 tokens", "en": "Today prompt tokens"},
    "usage_today_completion": {"zh": "当日输出 tokens", "en": "Today completion tokens"},
    "usage_month_total": {"zh": "近30天累计用量", "en": "30-day total usage"},
    "usage_range_totals": {"zh": "所选区间 · 输入 {prompt} · 输出 {completion} · 合计 {total} tokens", "en": "Selected range · prompt {prompt} · completion {completion} · total {total} tokens"},
    "usage_heatmap_tip": {
        "zh": "{date} · {tokens} tokens · {runs} 次运行",
        "en": "{date} · {tokens} tokens · {runs} runs",
    },
    "usage_heatmap_none": {"zh": "{date} · 无用量", "en": "{date} · no usage"},
    "usage_week_mon": {"zh": "一", "en": "M"},
    "usage_week_wed": {"zh": "三", "en": "W"},
    "usage_week_fri": {"zh": "五", "en": "F"},
    "usage_week_sun": {"zh": "日", "en": "S"},
    "usage_month_1": {"zh": "1月", "en": "Jan"},
    "usage_month_2": {"zh": "2月", "en": "Feb"},
    "usage_month_3": {"zh": "3月", "en": "Mar"},
    "usage_month_4": {"zh": "4月", "en": "Apr"},
    "usage_month_5": {"zh": "5月", "en": "May"},
    "usage_month_6": {"zh": "6月", "en": "Jun"},
    "usage_month_7": {"zh": "7月", "en": "Jul"},
    "usage_month_8": {"zh": "8月", "en": "Aug"},
    "usage_month_9": {"zh": "9月", "en": "Sep"},
    "usage_month_10": {"zh": "10月", "en": "Oct"},
    "usage_month_11": {"zh": "11月", "en": "Nov"},
    "usage_month_12": {"zh": "12月", "en": "Dec"},
    # ── 报告查看页：随手标记（原收藏偏好页融合）─────────────────────────
    "fav_like": {"zh": "喜欢", "en": "Like"},
    "reports_cards_hint": {"zh": "阅读时随手点 👍/👎 标记偏好；标记只进入偏好画像与学习模式。", "en": "Mark papers with 👍/👎 while reading; marks only feed the preference profile and learning mode."},
    "reports_raw_html": {"zh": "报告原文（HTML）", "en": "Original report (HTML)"},
    "fav_dislike": {"zh": "不喜欢", "en": "Dislike"},
    "fav_clear": {"zh": "清除标记", "en": "Clear mark"},
    "tab_analytics": {"zh": "数据分析", "en": "Analytics"},
    "an_diag_title": {"zh": "运行诊断", "en": "Run diagnostics"},
    "an_diag_recent_runs": {"zh": "近 10 次运行（完成/可用）", "en": "Last 10 runs (done/avail)"},
    "an_diag_recent_runs_help": {
        "zh": "分子为 completed 状态的运行数，分母为诊断窗口内可统计的运行数。",
        "en": "Completed runs over diagnosable runs in the recent window.",
    },
    "an_diag_failed_runs": {"zh": "近 10 次运行中有 {n} 次失败，请查看运行日志。", "en": "{n} of the last 10 runs failed; check the run logs."},
    "an_diag_latest_scan": {
        "zh": "最近一次扫描（{time}）：{ok}/{total} 个来源成功",
        "en": "Latest scan ({time}): {ok}/{total} sources succeeded",
    },
    "fav_state_like": {"zh": "已喜欢", "en": "Liked"},
    "fav_state_dislike": {"zh": "已不喜欢", "en": "Disliked"},
    "fav_summary_title": {"zh": "偏好汇总", "en": "Preference summary"},
    "fav_likes": {"zh": "喜欢", "en": "Likes"},
    "fav_dislikes": {"zh": "不喜欢", "en": "Dislikes"},
    "fav_top_authors": {"zh": "收藏作者 Top", "en": "Top authors"},
    "fav_top_categories": {"zh": "收藏领域 Top", "en": "Top categories"},
    "fav_matched_keywords": {
        "zh": "命中的主要关键词（按已喜欢论文标题统计）",
        "en": "Matched primary keywords (from liked paper titles)",
    },
    "fav_no_marks": {"zh": "还没有任何标记。", "en": "No marks yet."},
    "fav_no_keyword_hits": {
        "zh": "已喜欢的论文标题中没有命中主要关键词。",
        "en": "No primary keyword hits in liked paper titles.",
    },
    # ── llm.py ───────────────────────────────────────────────────────────
    "cheap_llm_title": {
        "zh": "低成本 LLM (CHEAP_LLM)",
        "en": "Low-Cost LLM (CHEAP_LLM)",
    },
    "cheap_llm_hint": {
        "zh": "用于快速评分和关键词生成，选择速度快、成本低的模型。",
        "en": "Used for quick scoring and keyword generation. Choose a fast, cheap model.",
    },
    "provider_preset": {"zh": "服务商预设", "en": "Provider Preset"},
    "base_url": {"zh": "Base URL", "en": "Base URL"},
    "api_key": {"zh": "API Key", "en": "API Key"},
    "secret_configured_keep_blank": {
        "zh": "已保存；为保护密钥，留空会保留原值。",
        "en": "Saved; leave this blank to keep the existing secret.",
    },
    "model_name": {"zh": "模型名称", "en": "Model Name"},
    "temperature": {"zh": "Temperature", "en": "Temperature"},
    "test_cheap_btn": {
        "zh": "测试 CHEAP_LLM 连接",
        "en": "Test CHEAP_LLM Connection",
    },
    "test_smart_btn": {
        "zh": "测试 SMART_LLM 连接",
        "en": "Test SMART_LLM Connection",
    },
    "testing_connection": {"zh": "测试连接中...", "en": "Testing connection..."},
    "smart_llm_title": {
        "zh": "高性能 LLM (SMART_LLM)",
        "en": "High-Performance LLM (SMART_LLM)",
    },
    "smart_llm_hint": {
        "zh": "用于深度分析和内容理解，选择能力强的模型。",
        "en": "Used for deep analysis and content understanding. Choose a capable model.",
    },
    "third_party_keys_title": {
        "zh": "第三方 API 密钥",
        "en": "Third-Party API Keys",
    },
    "third_party_keys_hint": {
        "zh": "可选的 API 密钥，用于增强功能。",
        "en": "Optional API keys for enhanced features.",
    },
    "openalex_email_label": {
        "zh": "OpenAlex Email（提升速率限制）",
        "en": "OpenAlex Email (improves rate limit)",
    },
    "s2_api_key_label": {
        "zh": "Semantic Scholar API Key",
        "en": "Semantic Scholar API Key",
    },
    "openalex_api_key_label": {
        "zh": "OpenAlex API Key",
        "en": "OpenAlex API Key",
    },
    "mineru_api_key_label": {
        "zh": "MinerU API Key",
        "en": "MinerU API Key",
    },
    "mineru_section_title": {
        "zh": "MinerU PDF 解析 API",
        "en": "MinerU PDF Parsing API",
    },
    "mineru_section_hint": {
        "zh": "MinerU 提供高质量云端 PDF 解析，Token 每 3 个月过期。点击「测试」可查看颟度余量和过期时间。",
        "en": "MinerU provides high-quality cloud PDF parsing. Token expires every 3 months. Click Test to check quota and expiry.",
    },
    "mineru_key_help": {
        "zh": "登录 mineru.net 获取 API Token",
        "en": "Get your API token from mineru.net",
    },
    "test_mineru_btn": {"zh": "测试 MinerU 连接", "en": "Test MinerU Connection"},
    "testing_mineru": {"zh": "连接 MinerU 中...", "en": "Connecting to MinerU..."},
    "mineru_expire_note": {
        "zh": "点击测试可查看 Token 过期日期和剩余颟度",
        "en": "Click Test to check token expiry and remaining quota",
    },
    # ── reports.py 导航按钞 ──
    "report_prev_day": {"zh": "← 前一天", "en": "← Prev Day"},
    "report_next_day": {"zh": "后一天 →", "en": "Next Day →"},
    "report_show_non_arxiv": {"zh": "显示非 ArXiv 来源", "en": "Show non-ArXiv sources"},
    "report_no_prev": {"zh": "已是最早的报告", "en": "No earlier reports"},
    "report_no_next": {"zh": "已是最新的报告", "en": "No newer reports"},
    # ── run_manager.py ──
    "run_manager_title": {"zh": "每日推送", "en": "Daily Push"},
    "run_now_section_title": {"zh": "每日研究", "en": "Daily Research"},
    "run_now_btn": {"zh": "立即运行", "en": "Run Now"},
    "stop_all_btn": {"zh": "停止所有进程", "en": "Stop All Processes"},
    "run_log_title": {"zh": "运行日志", "en": "Run Logs"},
    "daily_research_settings_title": {"zh": "每日研究设置", "en": "Daily Research Settings"},
    "daily_max_papers_label": {
        "zh": "本次最多处理论文数",
        "en": "Maximum papers per run",
    },
    "daily_max_papers_help": {
        "zh": "单次运行最多处理的论文数，默认 200：首次部署会一次性抓到大量历史论文，上限确保不会一次处理过多，堆积由后续运行消化，消化完每天正常处理全部新论文；0 表示不限。抓取始终完整执行，剩余论文保存在 SQLite 中等待下次处理。",
        "en": "Maximum papers processed per run, default 200: a fresh deployment first collects a large backlog at once, so the cap prevents huge single runs while later runs drain the queue; once drained, each day's new papers are processed in full. 0 means unlimited. Scans always run complete; excess papers stay queued in SQLite.",
    },
    "markdown_report_label": {"zh": "Markdown 报告", "en": "Markdown Report"},
    "no_logs_found": {"zh": "暂未找到日志文件。", "en": "No log files found."},
    "pid_killed": {"zh": "已向进程 {pid} 发送停止信号", "en": "Sent stop signal to process {pid}"},
    "no_running_process": {
        "zh": "未检测到正在运行的进程。",
        "en": "No running processes detected.",
    },
    "process_started": {
        "zh": "进程已启动，日志将实时生成。",
        "en": "Process started. Logs will appear shortly.",
    },
    # run_manager 状态标签
    "rm_status_running": {"zh": "运行中", "en": "Running"},
    "rm_status_stopped": {"zh": "已停止（文件未清除）", "en": "Stopped (file not cleaned)"},
    "rm_no_pid": {"zh": "无 PID", "en": "No PID"},
    "rm_process_running_label": {
        "zh": "面板触发的进程正在运行",
        "en": "Panel-triggered process is running",
    },
    "rm_no_panel_process": {
        "zh": "当前无面板触发的进程运行",
        "en": "No panel-triggered process running",
    },
    # run_manager Docker 模式
    "rm_docker_exec_hint": {
        "zh": "Docker 模式：通过 docker exec 在容器 {container} 中触发每日研究",
        "en": "Docker mode: triggers daily research via docker exec in container {container}",
    },
    "rm_docker_no_sock_warning": {
        "zh": (
            "⚠️ Docker 模式（无 Docker Socket）：无法直接启动任务。\n\n"
            "请在宿主机终端运行：\n"
            "`docker exec {container} python main.py --mode daily_research`"
        ),
        "en": (
            "⚠️ Docker mode (no Docker socket): cannot start tasks directly.\n\n"
            "Run on host terminal:\n"
            "`docker exec {container} python main.py --mode daily_research`"
        ),
    },
    "rm_docker_log_hint": {
        "zh": "（Docker 模式：确认 `logs/` 目录已通过卷挂载共享到此容器）",
        "en": "(Docker mode: ensure `logs/` is mounted into this container via a volume)",
    },
    # run_manager 日志选择器
    "rm_select_log_label": {"zh": "选择日志文件", "en": "Select log file"},
    "rm_select_log_help": {
        "zh": "按分类显示所有日志文件，括号内为最后修改时间",
        "en": "All log files grouped by type. Bracket shows last modified time.",
    },
    "rm_log_group_daily": {"zh": "每日研究日志", "en": "Daily Research"},
    "rm_log_group_trend": {"zh": "趋势分析日志", "en": "Trend Analysis"},
    "rm_log_group_other": {"zh": "其他日志", "en": "Other"},
    "rm_open_log_btn": {"zh": "打开", "en": "Open"},
    "rm_refresh_log_btn": {"zh": "刷新", "en": "Refresh"},
    "rm_close_log_btn": {"zh": "关闭", "en": "Close"},
    # ── trend_runner.py ──
    "trend_runner_title": {"zh": "趋势分析", "en": "Trend Analysis"},
    "trend_keywords_label": {"zh": "搜索关键词", "en": "Search Keywords"},
    "trend_keywords_help": {
        "zh": '多个关键词用空格分隔，引号包裹短语，如: quantum error correction "surface code"',
        "en": 'Multiple keywords separated by spaces. Quote phrases, e.g.: quantum error correction "surface code"',
    },
    "trend_date_from": {"zh": "起始日期", "en": "Date From"},
    "trend_date_to": {"zh": "截止日期", "en": "Date To"},
    "trend_categories_label": {
        "zh": "ArXiv 分类过滤（可选）",
        "en": "ArXiv Categories Filter (optional)",
    },
    "trend_categories_help": {
        "zh": "多个分类用空格分隔，如: quant-ph cs.AI",
        "en": "Multiple categories separated by spaces, e.g.: quant-ph cs.AI",
    },
    "trend_run_btn": {"zh": "开始趋势分析", "en": "Start Trend Analysis"},
    "trend_config_title": {"zh": "趋势分析配置", "en": "Trend Analysis Configuration"},
    "trend_sort_label": {"zh": "时间排序", "en": "Time Sort Order"},
    "trend_generate_tldr_label": {"zh": "生成 TLDR", "en": "Generate TLDR"},
    "trend_tldr_batch_label": {"zh": "TLDR 批次并发数", "en": "TLDR Batch Size"},
    "trend_skills_label": {"zh": "分析技能", "en": "Analysis skills"},
    "trend_output_formats_label": {"zh": "输出格式", "en": "Output Formats"},
    "trend_output_md_label": {"zh": "Markdown 报告", "en": "Markdown Report"},
    "trend_output_html_label": {"zh": "HTML 报告", "en": "HTML Report"},
    "trend_keywords_placeholder": {
        "zh": "quantum error correction · surface code",
        "en": "quantum error correction · surface code",
    },
    "trend_prompt_label": {"zh": "深度分析提示词", "en": "Deep-analysis prompt"},
    "trend_prompt_help": {
        "zh": "自定义「综合分析」的分析要求；留空使用内置综合分析指令。运行时立即生效，也可保存为模板复用。",
        "en": "Custom instructions for the comprehensive analysis; leave blank to use the built-in one. Applies immediately on run and can be saved as a template.",
    },
    "trend_prompt_placeholder": {
        "zh": "例如：请重点分析……并按……结构输出（留空使用内置指令）",
        "en": "e.g. Focus on ... and structure the output as ... (blank = built-in)",
    },
    "trend_prompt_template_label": {"zh": "提示词模板", "en": "Prompt template"},
    "trend_prompt_template_help": {
        "zh": "选择已保存的模板并应用到提示词框。",
        "en": "Pick a saved template to load it into the prompt box.",
    },
    "trend_prompt_template_none": {"zh": "默认（不自定义）", "en": "Default (no customization)"},
    "trend_prompt_add_title": {"zh": "新增自定义提示词", "en": "Add custom prompt"},
    "trend_prompt_add_btn": {"zh": "添加", "en": "Add"},
    "trend_prompt_template_delete": {"zh": "删除模板", "en": "Delete"},
    "trend_prompt_template_deleted": {"zh": "模板已删除", "en": "Template deleted"},
    "trend_prompt_template_save": {"zh": "保存为模板", "en": "Save template"},
    "trend_prompt_template_saved": {"zh": "模板已保存", "en": "Template saved"},
    "trend_prompt_template_name_label": {"zh": "模板名称", "en": "Template name"},
    "trend_prompt_template_name_placeholder": {"zh": "给当前提示词起个名字", "en": "Name this prompt"},
    "trend_prompt_template_name_required": {"zh": "请先填写模板名称。", "en": "Enter a template name first."},
    "trend_prompt_template_text_required": {"zh": "提示词为空，无需保存模板。", "en": "The prompt is empty; nothing to save."},
    # ── search.py ────────────────────────────────────────────────────────
    "search_settings_title": {"zh": "搜索设置", "en": "Search Settings"},
    "search_settings_hint": {
        "zh": "控制每日扫描的时间范围。",
        "en": "Control the time window scanned each day.",
    },
    "search_days_label": {"zh": "搜索最近 N 天", "en": "Search recent N days"},
    "search_days_help": {
        "zh": "推荐：1（每日）、7（每周）、30（每月）",
        "en": "Recommended: 1 (daily), 7 (weekly), 30 (monthly)",
    },
    "daily_scan_all_results": {"zh": "日报会处理时间范围内的全部新论文，不受篇数限制。", "en": "Daily research processes every new paper in the time window, with no item cap."},
    "data_sources_title": {"zh": "数据源", "en": "Data Sources"},
    "arxiv_settings_title": {"zh": "ArXiv 设置", "en": "ArXiv Settings"},
    "sh_hint": {"zh": "成功率按每个数据源最近 20 次扫描计算。", "en": "Success rates cover each source's last 20 scans."},
    "extra_sources_title": {"zh": "额外来源", "en": "Extra Sources"},
    "extra_sources_enabled": {"zh": "启用额外来源", "en": "Enable extra sources"},
    "extra_sources_help": {"zh": "关闭时保留已配置的来源但不抓取。", "en": "When off, configured sources are kept but not scanned."},
    "extra_sources_builtin_label": {"zh": "内置来源（多选）", "en": "Built-in sources (multi-select)"},
    "extra_sources_custom_title": {"zh": "自定义来源", "en": "Custom sources"},
    "extra_sources_add_title": {"zh": "➕ 新增自定义来源", "en": "➕ Add a custom source"},
    "extra_sources_add_code": {"zh": "来源代码（小写字母/数字/下划线）", "en": "Source code (lowercase/digits/underscore)"},
    "extra_sources_add_display": {"zh": "显示名称", "en": "Display name"},
    "extra_sources_add_full": {"zh": "期刊全名", "en": "Full journal name"},
    "extra_sources_add_issn": {"zh": "ISSN（逗号分隔）", "en": "ISSNs (comma separated)"},
    "extra_sources_add_btn": {"zh": "添加来源", "en": "Add source"},
    "extra_sources_added": {"zh": "已添加自定义来源。", "en": "Custom source added."},
    "extra_sources_removed": {"zh": "已移除自定义来源。", "en": "Custom source removed."},
    "extra_sources_summary": {
        "zh": "当前启用 {builtin} 个内置来源 + {custom} 个自定义来源。",
        "en": "{builtin} built-in + {custom} custom sources enabled.",
    },
    "extra_sources_invalid": {"zh": "来源定义无效", "en": "Invalid source definitions"},
    "reports_by_source_toggle": {
        "zh": "按数据源分类整理报告",
        "en": "Organize reports by source",
    },
    "reports_by_source_help": {
        "zh": "为每个数据源创建独立报告目录",
        "en": "Create separate report directories for each data source",
    },
    "arxiv_fetch_timeout_label": {
        "zh": "ArXiv 抓取超时（秒）",
        "en": "ArXiv fetch timeout (seconds)",
    },
    "arxiv_fetch_timeout_help": {
        "zh": "单次 ArXiv 抓取硬超时，超时后自动重试，避免任务长时间卡住",
        "en": "Hard timeout for one ArXiv fetch. Retries on timeout to avoid stuck runs.",
    },
    "arxiv_announcement_lookback_grace_label": {
        "zh": "ArXiv 延迟公告回看（天）",
        "en": "ArXiv delayed-announcement lookback (days)",
    },
    "arxiv_announcement_lookback_grace_help": {
        "zh": "额外回看可能因周末公告或 API 索引延迟而晚出现的论文。已交付的同一版本会自动去重，不会重复推送。",
        "en": "Rescan this many extra days for papers delayed by announcements or API indexing. Delivered exact versions are deduplicated.",
    },
    "huggingface_papers_source_notice": {
        "zh": "Hugging Face Papers 是可选的精选补充流，不是 arXiv 全量来源。启用后会完整分页；与本次 arXiv 扫描或已交付 arXiv 镜像重复的条目会自动跳过。",
        "en": "Hugging Face Papers is an optional curated supplementary feed, not a complete arXiv source. When enabled, all pages are fetched; mirrors already found or delivered through arXiv are skipped.",
    },
    "huggingface_papers_availability_lag_label": {
        "zh": "HF 日榜可用性滞后（天）",
        "en": "HF feed availability lag (days)",
    },
    "huggingface_papers_availability_lag_help": {
        "zh": "避免将尚未形成的当天日榜误判为“没有论文”；默认读取两天前及更早的日榜。",
        "en": "Avoid treating a not-yet-final daily feed as empty; by default, fetches feeds from two days ago and earlier.",
    },
    "huggingface_papers_lookback_grace_label": {
        "zh": "HF 日榜额外回看（天）",
        "en": "HF feed extra lookback (days)",
    },
    "huggingface_papers_lookback_grace_help": {
        "zh": "对已可用日榜额外重扫近期日期，以抵御展示或索引延迟；精确交付账本会去重。",
        "en": "Rescan additional already-available feeds for display or indexing delays; the exact delivery ledger prevents duplicates.",
    },
    "huggingface_papers_request_timeout_label": {
        "zh": "HF 单页请求超时（秒）",
        "en": "HF per-page request timeout (seconds)",
    },
    "huggingface_papers_request_interval_label": {
        "zh": "HF 请求间隔（秒）",
        "en": "HF request interval (seconds)",
    },
    "huggingface_papers_request_interval_help": {
        "zh": "只限制请求速率，不限制结果数量或处理范围。",
        "en": "Limits request rate only; it never caps result count or processing scope.",
    },
    "arxiv_domains_hint": {
        "zh": "ArXiv 分类代码，详见 https://arxiv.org/category_taxonomy",
        "en": "ArXiv category codes. See: https://arxiv.org/category_taxonomy",
    },
    "select_arxiv_cats": {
        "zh": "选择 ArXiv 分类",
        "en": "Select ArXiv categories",
    },
    "custom_domains_label": {
        "zh": "其他自定义分类（逗号分隔）",
        "en": "Additional custom domains (comma-separated)",
    },
    "custom_domains_help": {
        "zh": "输入不在列表中的 ArXiv 分类代码",
        "en": "Enter ArXiv category codes not in the list above",
    },
    # ── keywords.py ──────────────────────────────────────────────────────
    "primary_keywords_title": {"zh": "主要关键词", "en": "Primary Keywords"},
    "primary_keywords_hint": {
        "zh": "用于论文相关性评分的关键词，权重越高越重要。",
        "en": "Keywords used for paper relevance scoring. Higher weight = more importance.",
    },
    "keywords_textarea_label": {
        "zh": "主要关键词（每行一个）",
        "en": "Primary Keywords (one per line)",
    },
    "keywords_textarea_help": {
        "zh": "每行输入一个关键词，用于匹配论文标题和摘要。",
        "en": "Enter one keyword per line. These are matched against paper titles and abstracts.",
    },
    "keyword_weight_slider": {
        "zh": "主要关键词权重",
        "en": "Primary Keyword Weight",
    },
    "ref_extract_title": {
        "zh": "参考文献 PDF 关键词提取",
        "en": "Reference PDF Extraction",
    },
    "ref_extract_hint": {
        "zh": "自动从 data/reference_pdfs/ 中的参考 PDF 提取关键词。",
        "en": "Automatically extract keywords from reference PDFs in data/reference_pdfs/",
    },
    "enable_ref_extract": {
        "zh": "启用参考文献关键词提取",
        "en": "Enable reference keyword extraction",
    },
    "ref_extract_expander": {
        "zh": "参考文献提取设置",
        "en": "Reference Extraction Settings",
    },
    "max_extracted_kws": {
        "zh": "最大提取关键词数",
        "en": "Max extracted keywords",
    },
    "similarity_threshold_label": {
        "zh": "相似度阈值",
        "en": "Similarity threshold",
    },
    "similarity_threshold_help": {
        "zh": "相似度高于此阈值的关键词将被去重",
        "en": "Keywords above this similarity are de-duplicated",
    },
    "weight_distribution": {
        "zh": "**权重分布**",
        "en": "**Weight Distribution**",
    },
    "high_importance": {"zh": "*高重要性*", "en": "*High Importance*"},
    "medium_importance": {"zh": "*中重要性*", "en": "*Medium Importance*"},
    "low_importance": {"zh": "*低重要性*", "en": "*Low Importance*"},
    "weight_label": {"zh": "权重", "en": "Weight"},
    "count_label": {"zh": "数量", "en": "Count"},
    "kw_extracted_title": {"zh": "已提取的关键词", "en": "Extracted keywords"},
    "kw_extracted_empty": {
        "zh": "还没有从参考文献 PDF 提取到关键词；运行一次每日研究后会出现在这里。",
        "en": "No keywords extracted from reference PDFs yet; they appear here after a daily run.",
    },
    "kw_extracted_count": {"zh": "共 {total} 个（按权重排序）", "en": "{total} total (by weight)"},
    "research_context_title": {"zh": "研究背景", "en": "Research Context"},
    "research_context_hint": {
        "zh": "描述你的研究领域，帮助 LLM 更好地理解相关性。",
        "en": "Describe your research area to help the LLM better understand relevance.",
    },
    "research_context_label": {"zh": "研究背景", "en": "Research Context"},
    "research_context_placeholder": {
        "zh": "例如：我研究量子纠错和拓扑量子计算...",
        "en": "e.g., I study quantum error correction and topological quantum computing...",
    },
    # ── scoring.py ───────────────────────────────────────────────────────
    "scoring_title": {"zh": "资格与排序策略", "en": "Qualification & Ranking"},
    "scoring_hint": {
        "zh": "V2 仅用内容相关性决定资格；参考词与作者偏好只影响合格论文的排序。",
        "en": "V2 uses content relevance for qualification; reference terms and author preference only rank qualified papers.",
    },
    "score_strategy_label": {"zh": "评分策略", "en": "Scoring Strategy"},
    "score_strategy_help": {
        "zh": "V2 推荐用于新配置；旧策略仅用于兼容历史阈值和可逆迁移。",
        "en": "Use V2 for new configurations; legacy mode preserves historical thresholds for reversible migration.",
    },
    "core_relevance_info": {
        "zh": "资格要求：主关键词的加权平均相关度达标，且至少一个主关键词强匹配。参考关键词和专家作者都不能让无关论文通过。",
        "en": "Qualification requires a passing weighted average across primary keywords and one strong primary match. Reference terms and expert authors cannot pass an unrelated paper.",
    },
    "core_relevance_threshold_label": {"zh": "核心相关性门槛", "en": "Core Relevance Threshold"},
    "core_keyword_min_score_label": {"zh": "核心词强匹配门槛", "en": "Strong Core Match Threshold"},
    "reference_ranking_weight_label": {"zh": "参考词排序系数", "en": "Reference Ranking Weight"},
    "reference_ranking_weight_help": {
        "zh": "仅影响已合格论文的排序，不参与是否推荐。",
        "en": "Only ranks already qualified papers; it never affects recommendation eligibility.",
    },
    "core_relevance_no_primary_warning": {
        "zh": "尚未配置主要关键词。运行时会以全部关键词临时降级为核心集合；请在“关键词”页添加主要关键词，以获得稳定资格判定。",
        "en": "No primary keywords are configured. Runtime will temporarily fall back to all keywords as the core set; add primary keywords on the Keywords tab for stable qualification.",
    },
    "legacy_strategy_warning": {
        "zh": "兼容模式：作者加分和参考关键词仍会影响是否通过。只建议用于与既有历史结果对比或暂时回退。",
        "en": "Compatibility mode: author bonuses and reference keywords still affect qualification. Use only for historical comparison or a temporary rollback.",
    },
    "core_relevance_preview": {
        "zh": "V2：资格由主关键词内容相关度和强匹配共同决定；排序可额外使用参考词与作者偏好。",
        "en": "V2: primary-keyword relevance and a strong match decide qualification; reference terms and author preference may further rank qualified papers.",
    },
    "base_score_label": {"zh": "基础分", "en": "Base Score"},
    "weight_coeff_label": {"zh": "权重系数", "en": "Weight Coefficient"},
    "max_score_per_kw_label": {
        "zh": "每个关键词最高得分",
        "en": "Max Score Per Keyword",
    },
    "author_bonus_title": {"zh": "作者加分", "en": "Author Bonus"},
    "author_bonus_hint": {
        "zh": "给指定作者的论文额外加分。",
        "en": "Give extra points to papers by specific authors.",
    },
    "enable_author_bonus": {
        "zh": "启用作者加分",
        "en": "Enable author bonus",
    },
    "expert_authors_label": {
        "zh": "专家作者（每行一个）",
        "en": "Expert Authors (one per line)",
    },
    "expert_authors_help": {
        "zh": "包含这些作者的论文将获得额外分数",
        "en": "Papers with these authors receive bonus points",
    },
    "bonus_points_label": {"zh": "加分分值", "en": "Bonus Points"},
    "report_settings_title": {"zh": "报告设置", "en": "Report Settings"},
    "include_all_in_report": {
        "zh": "报告中包含所有论文（不仅是通过的）",
        "en": "Include all papers in report (not just passing)",
    },
    "include_all_help": {
        "zh": "关闭后，报告中只包含高于通过分数的论文",
        "en": "If disabled, only papers above the passing score are included",
    },
    # ── notifications.py ─────────────────────────────────────────────────
    "notif_settings_title": {"zh": "通知设置", "en": "Notification Settings"},
    "notif_settings_hint": {
        "zh": "运行完成时发送通知，在下方配置各通知渠道。",
        "en": "Send notifications when runs complete. Configure channels below.",
    },
    "enable_notifications": {
        "zh": "启用通知",
        "en": "Enable notifications",
    },
    "notify_success": {"zh": "成功时通知", "en": "Notify on success"},
    "notify_failure": {"zh": "失败时通知", "en": "Notify on failure"},
    "top_n_label": {"zh": "通知中展示 Top-N 篇论文", "en": "Top-N papers in notification"},
    "attach_reports": {
        "zh": "邮件附带报告文件",
        "en": "Attach report files to email",
    },
    "email_expander": {"zh": "邮件 (SMTP)", "en": "Email (SMTP)"},
    "enable_email": {"zh": "启用邮件", "en": "Enable Email"},
    "smtp_host_label": {"zh": "SMTP 服务器", "en": "SMTP Host"},
    "smtp_port_label": {"zh": "SMTP 端口", "en": "SMTP Port"},
    "use_tls_label": {"zh": "使用 TLS", "en": "Use TLS"},
    "smtp_user_label": {"zh": "SMTP 用户名", "en": "SMTP User"},
    "smtp_password_label": {"zh": "SMTP 密码", "en": "SMTP Password"},
    "from_address_label": {"zh": "发件人地址", "en": "From Address"},
    "to_addresses_label": {
        "zh": "收件人地址（逗号分隔）",
        "en": "To Addresses (comma-separated)",
    },
    "test_email_btn": {"zh": "测试邮件连接", "en": "Test Email Connection"},
    "testing_smtp": {"zh": "测试 SMTP 中...", "en": "Testing SMTP..."},
    "wechat_expander": {"zh": "企业微信", "en": "WeChat Work"},
    "enable_wechat": {"zh": "启用企业微信", "en": "Enable WeChat Work"},
    "webhook_url_label": {"zh": "Webhook URL", "en": "Webhook URL"},
    "dingtalk_expander": {"zh": "钉钉", "en": "DingTalk"},
    "enable_dingtalk": {"zh": "启用钉钉", "en": "Enable DingTalk"},
    "secret_optional_label": {"zh": "签名密钥（可选）", "en": "Secret (optional)"},
    "telegram_expander": {"zh": "Telegram", "en": "Telegram"},
    "enable_telegram": {"zh": "启用 Telegram", "en": "Enable Telegram"},
    "bot_token_label": {"zh": "Bot Token", "en": "Bot Token"},
    "chat_id_label": {"zh": "Chat ID", "en": "Chat ID"},
    "slack_expander": {"zh": "Slack", "en": "Slack"},
    "enable_slack": {"zh": "启用 Slack", "en": "Enable Slack"},
    "generic_webhook_expander": {
        "zh": "通用 Webhook",
        "en": "Generic Webhook",
    },
    "enable_generic_webhook": {
        "zh": "启用通用 Webhook",
        "en": "Enable Generic Webhook",
    },
    # ── advanced.py ──────────────────────────────────────────────────────
    "pdf_parser_title": {"zh": "PDF 解析器", "en": "PDF Parser"},
    "pdf_parser_hint": {
        "zh": "选择解析研究论文 PDF 的方式。",
        "en": "Choose how to parse research paper PDFs.",
    },
    "parser_mode_label": {"zh": "解析器模式", "en": "Parser Mode"},
    "parser_mode_help": {
        "zh": "mineru：云端 API（质量更高）| pymupdf：本地（无需网络）",
        "en": "mineru: cloud API (higher quality) | pymupdf: local (no network)",
    },
    "mineru_version_label": {
        "zh": "MinerU 模型版本",
        "en": "MinerU Model Version",
    },
    "mineru_version_help": {
        "zh": "pipeline：速度快 | vlm：更精准（消耗更多配额）",
        "en": "pipeline: fast | vlm: more accurate (uses more quota)",
    },
    "pdf_download_max_mb_label": {
        "zh": "PDF 下载大小上限（MB）",
        "en": "PDF Download Size Limit (MB)",
    },
    "pdf_download_max_mb_help": {
        "zh": "深度分析下载的单个 PDF 上限；超限或非 PDF 响应会保留论文供后续重试。",
        "en": "Per-PDF deep-analysis limit; oversized or non-PDF responses remain retryable.",
    },
    "concurrency_title": {"zh": "并发设置", "en": "Concurrency"},
    "concurrency_hint": {
        "zh": "LLM 评分的并行处理，注意 API 速率限制。",
        "en": "Parallel processing for LLM scoring. Watch for API rate limits.",
    },
    "enable_concurrency": {
        "zh": "启用并发处理",
        "en": "Enable concurrent processing",
    },
    "worker_threads_label": {"zh": "工作线程数", "en": "Worker threads"},
    "worker_threads_help": {
        "zh": "推荐：3-5，过高可能触发速率限制。",
        "en": "Recommended: 3-5. Higher values may trigger rate limits.",
    },
    "llm_pool_title": {"zh": "LLM 请求池", "en": "LLM Request Pool"},
    "llm_pool_hint": {
        "zh": "全局限制 LLM 请求速率，避免并发任务触发 API 限流。",
        "en": "Globally limit LLM request rate to avoid API throttling.",
    },
    "enable_llm_pool": {"zh": "启用 LLM 请求池", "en": "Enable LLM request pool"},
    "llm_rpm_label": {"zh": "每分钟请求数", "en": "Requests per minute"},
    "llm_slow_wait_label": {"zh": "慢等待日志阈值（秒）", "en": "Slow-wait log threshold (seconds)"},
    "daily_persistence_title": {
        "zh": "每日研究持久化",
        "en": "Daily Research Persistence",
    },
    "daily_persistence_hint": {
        "zh": "保存论文级评分与分析进度，用于断点续跑和失败恢复。",
        "en": "Save paper-level scoring and analysis progress for resume and recovery.",
    },
    "enable_daily_persistence": {
        "zh": "启用论文级持久化",
        "en": "Enable paper-level persistence",
    },
    "daily_db_path_label": {"zh": "持久化数据库路径", "en": "Persistence database path"},
    "daily_deep_analysis_label": {"zh": "启用每日深度分析", "en": "Enable daily deep analysis"},
    "advanced_reports_title": {"zh": "功能开关", "en": "Feature Toggles"},
    "html_reports_label": {"zh": "HTML 报告", "en": "HTML reports"},
    "token_tracking_label": {"zh": "Token 用量追踪", "en": "Token tracking"},
    "auto_update_label": {"zh": "自动更新检查", "en": "Auto-update check"},
    "kw_tracker_title": {
        "zh": "关键词趋势追踪",
        "en": "Keyword Trend Tracking",
    },
    "enable_kw_tracker": {
        "zh": "启用关键词追踪",
        "en": "Enable keyword tracking",
    },
    "kw_tracker_expander": {
        "zh": "关键词追踪设置",
        "en": "Keyword Tracker Settings",
    },
    "ai_normalization_label": {
        "zh": "AI 归一化",
        "en": "AI normalization",
    },
    "normalization_batch_label": {
        "zh": "归一化批次大小",
        "en": "Normalization batch size",
    },
    "trend_view_days_label": {
        "zh": "默认趋势视图天数",
        "en": "Default trend view (days)",
    },
    "bar_chart_top_n_label": {
        "zh": "柱状图 Top-N",
        "en": "Bar chart top-N",
    },
    "trend_chart_top_n_label": {
        "zh": "趋势图 Top-N",
        "en": "Trend chart top-N",
    },
    "enable_trend_reports_label": {
        "zh": "启用趋势报告",
        "en": "Enable trend reports",
    },
    "report_frequency_label": {
        "zh": "报告频率",
        "en": "Report frequency",
    },
    "retry_title": {"zh": "重试与日志", "en": "Retry & Logging"},
    "max_retries_label": {"zh": "最大重试次数", "en": "Max retry attempts"},
    "min_wait_label": {"zh": "最短等待（秒）", "en": "Min wait (seconds)"},
    "max_wait_label": {"zh": "最长等待（秒）", "en": "Max wait (seconds)"},
    "run_lock_max_age_label": {
        "zh": "运行锁超龄告警阈值（小时）",
        "en": "Run-lock long-run warning threshold (hours)",
    },
    "run_lock_max_age_help": {
        "zh": "同一任务超过该时长时，后续同类任务会告警并跳过；不会按 PID 自动终止进程。",
        "en": (
            "If a task exceeds this duration, a later matching run warns and "
            "skips it; no process is terminated by PID automatically."
        ),
    },
    "log_rotation_label": {"zh": "日志轮转方式", "en": "Log rotation"},
    "log_retention_label": {"zh": "日志保留天数", "en": "Log retention (days)"},
    "trend_research_title": {
        "zh": "趋势研究模式",
        "en": "Trend Research Mode",
    },
    "trend_date_range_label": {
        "zh": "默认日期范围（天）",
        "en": "Default date range (days)",
    },
    "trend_sort_order_label": {"zh": "排序方式", "en": "Sort order"},
    "trend_max_results_label": {"zh": "最大结果数", "en": "Max results"},
    "trend_report_position_label": {
        "zh": "报告位置",
        "en": "Report position",
    },
    "generate_tldr_label": {"zh": "生成 TLDR", "en": "Generate TLDR"},
    "tldr_batch_size_label": {
        "zh": "TLDR 批次大小",
        "en": "TLDR batch size",
    },
    "enabled_skills_label": {
        "zh": "**启用的分析技能**",
        "en": "**Enabled Analysis Skills**",
    },
    # Skill names
    "skill_comprehensive_analysis": {
        "zh": "综合趋势分析",
        "en": "Comprehensive Trend Analysis",
    },
    # ── reports.py ────────────────────────────────────────────────────────
    "reports_title": {"zh": "报告查看", "en": "Report Viewer"},
    "reports_hint": {
        "zh": "浏览并在线预览所有已生成的 HTML 报告，包括每日研究报告、趋势分析报告和关键词趋势报告。",
        "en": (
            "Browse and preview all generated HTML reports: daily research, trend "
            "analysis, and keyword trend."
        ),
    },
    "reports_refresh": {"zh": "刷新文件列表", "en": "Refresh File List"},
    "reports_empty": {
        "zh": "data/reports/ 目录下暂无 HTML 报告，请先运行一次研究任务。",
        "en": "No HTML reports found in data/reports/. Run a research task first.",
    },
    "reports_empty_type": {"zh": "暂无报告", "en": "No reports"},
    "reports_count_unit": {"zh": "份", "en": "reports"},
    "reports_preview_btn": {"zh": "▶ 预览", "en": "▶ Preview"},
    "reports_dir_label": {"zh": "报告目录", "en": "Reports directory"},
    "rtype_daily": {"zh": "每日研究", "en": "Daily Research"},
    "rtype_trend": {"zh": "趋势分析", "en": "Trend Analysis"},
    "rtype_keyword_trend": {"zh": "关键词趋势", "en": "Keyword Trend"},
    "reports_meta_expander": {"zh": "运行参数", "en": "Run Parameters"},
    "meta_keyword": {"zh": "关键词", "en": "Keyword"},
    "meta_date_range": {"zh": "日期范围", "en": "Date Range"},
    "meta_papers": {"zh": "论文数量", "en": "Paper Count"},
    "reports_mtime": {"zh": "生成时间", "en": "Generated"},
    "reports_height": {"zh": "预览高度", "en": "Preview Height"},
    "reports_load_error": {"zh": "报告加载失败", "en": "Failed to load report"},
    # ── trend_runner.py 新增 i18n ──
    "tr_section_params": {"zh": "分析参数", "en": "Analysis Parameters"},
    "tr_section_run_control": {"zh": "运行控制", "en": "Run Control"},
    "tr_categories_placeholder": {
        "zh": "例如: quant-ph cs.AI（留空则不过滤）",
        "en": "e.g. quant-ph cs.AI (leave empty for no filter)",
    },
    "tr_default_date_range_days_label": {
        "zh": "默认时间范围（天）",
        "en": "Default date range (days)",
    },
    "tr_default_date_range_days_help": {
        "zh": "保存后作为新建分析的默认时间范围",
        "en": "Saved as the default date range for new analyses",
    },
    "tr_stop_btn_label": {"zh": "⏹ 停止趋势分析", "en": "⏹ Stop Trend Analysis"},
    "tr_locks_found": {
        "zh": "当前有 {n} 个趋势分析任务锁文件存在（可能正在运行或异常退出）。",
        "en": "{n} trend analysis lock file(s) found (may be running or crashed).",
    },
    "tr_stop_signal_sent": {
        "zh": "已向趋势分析进程 PID={pid} 发送停止信号 ({name})",
        "en": "Sent stop signal to trend analysis process PID={pid} ({name})",
    },
    "tr_stop_failed": {"zh": "停止 PID={pid} 失败: {err}", "en": "Failed to stop PID={pid}: {err}"},
    "tr_no_running_trend": {
        "zh": "未检测到正在运行的趋势分析进程。",
        "en": "No running trend analysis processes detected.",
    },
    "tr_err_no_keywords": {"zh": "请输入搜索关键词。", "en": "Please enter search keywords."},
    "tr_err_date_range": {
        "zh": "起始日期不能晚于截止日期。",
        "en": "Start date cannot be later than end date.",
    },
    "tr_started": {
        "zh": "趋势分析已启动！PID={pid}，请在「报告查看」Tab 中查看结果。",
        "en": "Trend analysis started! PID={pid}. Check the Reports tab for results.",
    },
    "tr_start_failed": {"zh": "启动失败: {err}", "en": "Failed to start: {err}"},
    # skill labels (also fix time_evolution and key_researchers missing earlier)
    # run_manager 触发文件机制新 key
    "rm_trigger_pending": {
        "zh": "已发送运行请求，等待主研究容器响应（最多 10 秒）...",
        "en": "Run request sent, waiting for researcher container to respond (up to 10s)...",
    },
    "rm_trigger_pending_short": {
        "zh": "等待主容器响应中...",
        "en": "Waiting for main container...",
    },
    "rm_trigger_sent": {
        "zh": "✅ 已发送运行请求！主研究容器将在 5-10 秒内启动任务，日志将在「运行日志」区实时更新。",
        "en": "✅ Run request sent! The researcher container will start the task within 5-10 seconds. Check the Run Logs section.",
    },
    "rm_trigger_failed": {
        "zh": "❌ 写入触发文件失败",
        "en": "❌ Failed to write trigger file",
    },
    "rm_launch_failed": {
        "zh": "❌ 启动失败，请查看运行日志",
        "en": "❌ Could not start the run; check the run log",
    },
    "rm_already_running_warn": {
        "zh": "检测到已有一个进程在运行（PID={pid}），请先停止后再启动。",
        "en": "A process is already running (PID={pid}). Please stop it before starting a new one.",
    },
    # ── run_manager v2 新增 key ──
    "rm_trigger_stale": {
        "zh": "触发文件已存在 {n} 秒仍未被主容器消费，可能主研究容器未运行。请检查后清除。",
        "en": "Trigger file has been pending for {n}s without being consumed. The researcher container may not be running. Please check and clear it.",
    },
    "rm_clear_trigger_btn": {"zh": "🗑 清除触发文件", "en": "🗑 Clear Trigger File"},
    "rm_trigger_sent_short": {
        "zh": "已发送运行请求！主容器将在 5-10s 内启动",
        "en": "Run request sent! Container will start within 5-10s",
    },
    "rm_last_run_at": {"zh": "上次运行完成", "en": "Last run completed"},
    "rm_clean_lock_btn": {"zh": "清理", "en": "Clean"},
    "rm_clean_lock_help": {
        "zh": "删除此已停止任务的锁文件（任务已结束，锁文件残留）",
        "en": "Remove this stale lock file (task finished, file not cleaned up)",
    },
    "rm_log_group_primary": {"zh": "系统 & 运行日志", "en": "System & Run Logs"},
    "rm_log_group_secondary": {"zh": "其他日志", "en": "Other Logs"},
    "rm_log_group_system": {"zh": "系统日志", "en": "System Logs"},
    "rm_log_group_runs": {"zh": "运行日志", "en": "Run Logs"},
    "rm_no_log_selected": {
        "zh": "请从上方选择一个日志文件以查看内容。",
        "en": "Select a log file above to view its content.",
    },
    "rm_log_file_missing": {
        "zh": "⚠️ 日志文件已不存在，可能已被清理。",
        "en": "⚠️ Log file no longer exists, it may have been cleaned up.",
    },
    "rm_scan_receipts_empty": {
        "zh": "尚无可显示的扫描收据；下一次启用持久化的日报运行后会出现。",
        "en": "No scan receipt is available yet. It will appear after the next persistent daily run.",
    },
    "rm_scan_receipts_legacy": {
        "zh": "本地数据库尚未包含扫描收据表；下一次升级后的持久化日报运行会建立可观测记录。",
        "en": "The local database does not yet contain scan receipts. A persistent daily run after the upgrade will create observable records.",
    },
    "rm_scan_receipt_candidates": {"zh": "新候选", "en": "New candidates"},
    "rm_scan_receipt_status": {"zh": "状态", "en": "Status"},
    "rm_scan_receipt_source": {"zh": "来源", "en": "Source"},
    "rm_scan_receipt_scanned_at": {"zh": "扫描时间", "en": "Scanned at"},
    "rm_health_empty": {
        "zh": "尚无可用于运行健康摘要的本地持久化数据库。",
        "en": "No local persistence database is available for operational health yet.",
    },
    "rm_health_load_error": {
        "zh": "无法读取本地运行健康摘要",
        "en": "Could not read local operational health",
    },
    "rm_health_qualification_rate": {"zh": "评分通过率", "en": "Qualification rate"},
    "rm_health_notification_backlog": {"zh": "待处理通知", "en": "Open notifications"},
    # ── proxy.py ──────────────────────────────────────────────────────
    "proxy_title": {"zh": "网络代理设置", "en": "Network Proxy Settings"},
    "proxy_enable_label": {"zh": "启用网络代理", "en": "Enable Network Proxy"},
    "proxy_url_label": {"zh": "代理地址", "en": "Proxy URL"},
    "proxy_url_help": {
        "zh": "支持 HTTP 代理（http://host:port）和 SOCKS5 代理（socks5://host:port）",
        "en": "Supports HTTP proxy (http://host:port) and SOCKS5 proxy (socks5://host:port)",
    },
    "proxy_no_proxy_label": {"zh": "不使用代理的地址", "en": "No Proxy"},
    "proxy_no_proxy_help": {
        "zh": "每行一个地址，这些地址将不使用代理",
        "en": "One address per line, these addresses will bypass the proxy",
    },
    "proxy_scope_title": {"zh": "代理范围控制", "en": "Proxy Scope Control"},
    "proxy_scope_hint": {
        "zh": "选择哪些服务使用代理。可以按需为不同服务分别启用或禁用代理。",
        "en": "Select which services use the proxy. Enable or disable proxy for each service independently.",
    },
    "proxy_arxiv_label": {"zh": "ArXiv API", "en": "ArXiv API"},
    "proxy_arxiv_help": {
        "zh": "ArXiv 论文抓取 API（export.arxiv.org）",
        "en": "ArXiv paper fetching API (export.arxiv.org)",
    },
    "proxy_openalex_label": {"zh": "OpenAlex API", "en": "OpenAlex API"},
    "proxy_openalex_help": {
        "zh": "OpenAlex 期刊论文数据源",
        "en": "OpenAlex journal paper data source",
    },
    "proxy_huggingface_papers_label": {
        "zh": "Hugging Face Papers API",
        "en": "Hugging Face Papers API",
    },
    "proxy_huggingface_papers_help": {
        "zh": "Hugging Face Papers 可选补充论文流",
        "en": "Hugging Face Papers optional supplementary feed",
    },
    "proxy_semantic_scholar_label": {
        "zh": "Semantic Scholar API",
        "en": "Semantic Scholar API",
    },
    "proxy_semantic_scholar_help": {
        "zh": "Semantic Scholar TLDR 增强功能",
        "en": "Semantic Scholar TLDR enrichment",
    },
    "proxy_llm_api_label": {"zh": "LLM API", "en": "LLM API"},
    "proxy_llm_api_help": {
        "zh": "LLM 大模型 API（评分、分析等）",
        "en": "LLM API calls (scoring, analysis, etc.)",
    },
    "proxy_notifications_label": {"zh": "通知 Webhook", "en": "Notification Webhooks"},
    "proxy_notifications_help": {
        "zh": "企业微信、钉钉、Telegram 等通知推送",
        "en": "WeChat Work, DingTalk, Telegram, and other notification pushes",
    },
    "proxy_webdav_label": {"zh": "WebDAV 同步", "en": "WebDAV Sync"},
    "proxy_webdav_help": {
        "zh": "WebDAV 配置和数据备份/恢复请求",
        "en": "WebDAV configuration and data backup/restore requests",
    },
    "proxy_update_check_label": {"zh": "检查更新", "en": "Update Check"},
    "proxy_update_check_help": {
        "zh": "GitHub 版本更新检查（需访问 api.github.com）",
        "en": "GitHub version update check (requires access to api.github.com)",
    },
    "proxy_details_collapsed": {
        "zh": "代理未启用；代理地址、不使用代理的地址和服务范围已折叠。",
        "en": "Proxy is disabled; URL, no-proxy addresses, and service scope are collapsed.",
    },
    # ── data_management.py ───────────────────────────────────────────
    "tab_data_management": {"zh": "数据管理", "en": "Data Management"},
    "dm_export_title": {"zh": "配置导出", "en": "Config Export"},
    "dm_export_hint": {
        "zh": "一键打包导出 config.json 和 .env 配置文件。",
        "en": "One-click export of config.json and .env as a zip archive.",
    },
    "dm_export_btn": {"zh": "📦 导出配置", "en": "📦 Export Config"},
    "dm_export_no_files": {
        "zh": "未找到可导出的配置文件",
        "en": "No config files found to export",
    },
    "dm_export_contents": {
        "zh": "包含: config.json + .env",
        "en": "Contains: config.json + .env",
    },
    "dm_webdav_title": {"zh": "WebDAV 同步", "en": "WebDAV Sync"},
    "dm_webdav_hint": {
        "zh": "将配置和数据文件同步到 WebDAV 服务器，实现跨设备备份与恢复。默认关闭，只有启用后才会同步。",
        "en": "Sync config and data files to a WebDAV server for cross-device backup and restore. Disabled by default.",
    },
    "dm_webdav_enable": {"zh": "启用 WebDAV 同步", "en": "Enable WebDAV Sync"},
    "dm_webdav_url_label": {"zh": "WebDAV 服务器地址", "en": "WebDAV Server URL"},
    "dm_webdav_username_label": {"zh": "用户名", "en": "Username"},
    "dm_webdav_password_label": {"zh": "密码 / 应用密码", "en": "Password / App Password"},
    "dm_webdav_remote_path": {"zh": "远程存储路径", "en": "Remote Path"},
    "dm_webdav_remote_path_help": {
        "zh": "WebDAV 服务器上的存储根路径",
        "en": "Root storage path on the WebDAV server",
    },
    "dm_webdav_sync_settings": {"zh": "同步设置", "en": "Sync Settings"},
    "dm_webdav_sync_mode": {"zh": "同步模式", "en": "Sync Mode"},
    "dm_webdav_mode_manual": {"zh": "手动同步", "en": "Manual"},
    "dm_webdav_mode_scheduled": {"zh": "定时同步", "en": "Scheduled"},
    "dm_webdav_mode_after_report": {"zh": "每日报告完成后", "en": "After Daily Report"},
    "dm_webdav_sync_time": {"zh": "定时同步时间", "en": "Scheduled Sync Time"},
    "dm_webdav_sync_time_help": {
        "zh": "选择每天自动同步的时间（小时:分钟）",
        "en": "Select the daily auto-sync time (hour:minute)",
    },
    "dm_webdav_scope_title": {"zh": "同步范围", "en": "Sync Scope"},
    "dm_webdav_sync_configs_label": {"zh": "配置文件 (config.json)", "en": "Config (config.json)"},
    "dm_webdav_sync_history_label": {"zh": "历史记录 (history/)", "en": "History (history/)"},
    "dm_webdav_sync_keywords_label": {"zh": "关键词数据 (keywords/)", "en": "Keywords (keywords/)"},
    "dm_webdav_sync_reports_label": {"zh": "报告文件 (reports/)", "en": "Reports (reports/)"},
    "dm_webdav_test_btn": {"zh": "🔌 测试连接", "en": "🔌 Test Connection"},
    "dm_webdav_upload_btn": {"zh": "⬆️ 上传同步", "en": "⬆️ Upload"},
    "dm_webdav_download_btn": {"zh": "⬇️ 下载恢复", "en": "⬇️ Download"},
    "dm_webdav_not_configured": {
        "zh": "WebDAV 未配置或未启用。请填写服务器地址和凭据。",
        "en": "WebDAV not configured or not enabled. Please fill in the server URL and credentials.",
    },
    "dm_webdav_test_ok": {"zh": "✅ WebDAV 连接成功！", "en": "✅ WebDAV connection successful!"},
    "dm_webdav_test_fail": {"zh": "❌ WebDAV 连接失败", "en": "❌ WebDAV connection failed"},
    "dm_webdav_missing_lib": {
        "zh": "❌ 缺少 webdavclient3 库。请运行: pip install webdavclient3",
        "en": "❌ Missing webdavclient3 library. Run: pip install webdavclient3",
    },
    "dm_webdav_syncing": {"zh": "正在同步...", "en": "Syncing..."},
    "dm_webdav_sync_done": {"zh": "✅ 同步完成", "en": "✅ Sync completed"},
    "dm_webdav_sync_partial": {"zh": "⚠️ 部分同步成功", "en": "⚠️ Partial sync success"},
    "dm_webdav_sync_error": {"zh": "❌ 同步失败", "en": "❌ Sync failed"},
    "dm_backup_title": {"zh": "数据库备份", "en": "Database Backup"},
    "dm_backup_hint": {
        "zh": "每日运行结束后自动对 SQLite 数据库做 gzip 压缩备份，本地与 WebDAV 各保留指定份数；也可手动立即备份。",
        "en": "Automatically create a gzip-compressed SQLite backup after each daily run, keeping a bounded number of copies locally and on WebDAV; manual backup is also available.",
    },
    "dm_backup_enable": {"zh": "启用自动备份", "en": "Enable automatic backup"},
    "dm_backup_upload_label": {"zh": "压缩后上传 WebDAV（节省流量）", "en": "Upload compressed backup to WebDAV (saves traffic)"},
    "dm_backup_keep_label": {"zh": "保留份数", "en": "Copies to keep"},
    "dm_backup_keep_help": {
        "zh": "本地与 WebDAV 各保留的备份份数，超出后自动轮转删除最旧的备份",
        "en": "Number of backups kept locally and on WebDAV; the oldest are rotated out",
    },
    "dm_backup_now_btn": {"zh": "🗄️ 立即备份", "en": "🗄️ Back Up Now"},
    "dm_backup_existing": {"zh": "现有备份（最近 10 份）", "en": "Existing backups (latest 10)"},
    "dm_backup_none": {"zh": "暂无备份。运行一次每日研究或点击上方按钮即可生成。", "en": "No backups yet. Run the daily research once or use the button above."},
    "dm_backup_col_name": {"zh": "文件名", "en": "File"},
    "dm_backup_col_size": {"zh": "大小", "en": "Size"},
    "dm_backup_col_time": {"zh": "时间", "en": "Time"},
    "dm_backup_running": {"zh": "正在创建压缩备份...", "en": "Creating compressed backup..."},
    "dm_backup_done_local": {"zh": "✅ 备份完成（仅本地）", "en": "✅ Backup created (local only)"},
    "dm_backup_done_uploaded": {"zh": "✅ 备份完成并已上传 WebDAV", "en": "✅ Backup created and uploaded to WebDAV"},
    "dm_backup_done_upload_failed": {
        "zh": "⚠️ 本地备份已完成，但 WebDAV 上传失败：{}",
        "en": "⚠️ Local backup created, but the WebDAV upload failed: {}",
    },
    "dm_backup_local_only": {
        "zh": "WebDAV 凭据不完整，本次仅做本地备份",
        "en": "WebDAV credentials incomplete; backing up locally only",
    },
    "dm_backup_skip_reason": {"zh": "未创建备份：{}", "en": "Backup not created: {}"},
    "dm_backup_failed": {"zh": "❌ 备份失败", "en": "❌ Backup failed"},
    "ps_title": {"zh": "论文检索", "en": "Paper Search"},
    "ps_hint": {"zh": "在已处理论文的标题、作者、摘要、TLDR 与提取关键词中检索。", "en": "Search titles, authors, abstracts, TLDRs and extracted keywords of processed papers."},
    "ps_query_label": {"zh": "检索词", "en": "Query"},
    "ps_query_placeholder": {
        "zh": "例如：diffusion model、作者名、关键词……",
        "en": "e.g. diffusion model, an author name, a keyword...",
    },
    "ps_source_label": {"zh": "来源", "en": "Source"},
    "ps_source_all": {"zh": "全部来源", "en": "All sources"},
    "ps_date_label": {"zh": "处理日期范围", "en": "Processed date range"},
    "ps_date_help": {
        "zh": "可选；按论文被处理入库的日期过滤",
        "en": "Optional; filters by the date the paper was processed",
    },
    "ps_min_score_label": {"zh": "最低总分", "en": "Minimum score"},
    "ps_min_score_help": {
        "zh": "0 表示不过滤；按历史评分总分过滤",
        "en": "0 disables the filter; uses the historical total score",
    },
    "ps_liked_only_label": {"zh": "只看收藏 (👍)", "en": "Liked only (👍)"},
    "ps_search_btn": {"zh": "🔍 检索", "en": "🔍 Search"},
    "ps_idle_hint": {"zh": "输入条件后点击检索。留空检索词可只用过滤器浏览全库。", "en": "Enter criteria and click Search. An empty query lists the whole archive with filters."},
    "ps_result_title": {"zh": "共 {total} 篇匹配", "en": "{total} matching papers"},
    "ps_no_match": {"zh": "没有匹配的论文。", "en": "No matching papers."},
    "ps_no_data": {"zh": "暂无数据库或尚未处理任何论文。", "en": "No database yet, or no papers processed."},
    "ps_search_failed": {"zh": "检索失败：{}", "en": "Search failed: {}"},
    "ps_col_source": {"zh": "来源", "en": "Source"},
    "ps_col_completed": {"zh": "处理时间", "en": "Processed"},
    "ps_col_published": {"zh": "发表日期", "en": "Published"},
    "ps_col_strategy": {"zh": "评分策略", "en": "Strategy"},
    "ps_col_authors": {"zh": "作者", "en": "Authors"},
    "ps_col_keywords": {"zh": "提取关键词", "en": "Extracted keywords"},
    "ps_col_categories": {"zh": "分类", "en": "Categories"},
    "ps_link_abs": {"zh": "论文页", "en": "Abstract"},
    "ps_link_pdf": {"zh": "PDF", "en": "PDF"},
    "ps_prev_page": {"zh": "← 上一页", "en": "← Previous"},
    "ps_next_page": {"zh": "下一页 →", "en": "Next →"},
    "ps_page_info": {"zh": "第 {page} / {pages} 页", "en": "Page {page} of {pages}"},
    "sh_title": {"zh": "数据源健康", "en": "Source Health"},
    "sh_no_data": {"zh": "暂无数据库，运行一次每日研究后可见。", "en": "No database yet; visible after the first daily research run."},
    "sh_no_receipts": {"zh": "尚无扫描收据。运行一次每日研究后可见。", "en": "No scan receipts yet; visible after the first daily research run."},
    "sh_load_failed": {"zh": "健康数据加载失败：{}", "en": "Failed to load health data: {}"},
    "sh_success_rate": {"zh": "成功率", "en": "Success"},
    "sh_success_rate_help": {"zh": "最近 20 次扫描中成功终态的占比", "en": "Succeeded terminal scans out of the last 20"},
    "sh_new_candidates": {"zh": "最近新增", "en": "New papers"},
    "sh_new_candidates_help": {"zh": "最近一次成功扫描新入队的候选论文数", "en": "Candidates newly queued by the newest successful scan"},
    "sh_last_scan": {"zh": "最近扫描：{time}", "en": "Last scan: {time}"},
    "sh_last_error": {"zh": "最近一次错误", "en": "Newest error"},
    "sh_window_note": {"zh": "共汇总 {scans} 条扫描收据（每个来源最近 20 次）。", "en": "Summarized {scans} scan receipts (last 20 per source)."},
    "learned_strategy_info": {"zh": "学习模式：在旧版加权总分之上叠加学习库修正。学习库来自你的收藏/不喜欢与 v1 及格历史，学到的权重有上限且始终低于直接配置的评分关键词。", "en": "Learned mode adds a learned-library adjustment on top of the legacy weighted score. The library learns from your likes/dislikes and legacy v1 passes; learned weights are capped so they always matter less than directly configured keywords."},
    "learned_weight_dampening_label": {"zh": "学习权重衰减系数", "en": "Learned weight dampening"},
    "learned_weight_dampening_help": {
        "zh": "0-1；学习到的权重统一乘以该系数，越小学习项影响越弱",
        "en": "0-1; learned weights are scaled by this factor; smaller means weaker influence",
    },
    "learned_term_weight_cap_label": {"zh": "学习单项限幅", "en": "Learned term weight cap"},
    "learned_term_weight_cap_help": {
        "zh": "单个学习关键词/作者的最大绝对权重，超出按此截断",
        "en": "Maximum absolute weight of a single learned keyword/author",
    },
    "learned_library_keywords": {"zh": "学习关键词库（前 10）", "en": "Learned keywords (top 10)"},
    "learned_library_authors": {"zh": "学习作者库（前 10）", "en": "Learned authors (top 10)"},
    "learned_library_empty": {
        "zh": "学习库为空：标记收藏/不喜欢或运行几次 v1 评分后，这里会逐步出现带权重的关键词与作者。",
        "en": "The learned library is empty: mark likes/dislikes or run a few v1-scored days and weighted keywords/authors will accumulate here.",
    },
    "learned_library_note": {
        "zh": "权重 = 收藏(+1)/不喜欢(-1)/v1 及格(+0.25) 信号之和；评分时按限幅与衰减生效。",
        "en": "Weight = sum of like(+1)/dislike(-1)/v1-pass(+0.25) signals; applied with the cap and dampening at scoring time.",
    },
}


def t(key: str) -> str:
    """Return the translated string for the current language."""
    lang = st.session_state.get("lang", "zh")
    entry = _TRANSLATIONS.get(key, {})
    return entry.get(lang, entry.get("en", key))
