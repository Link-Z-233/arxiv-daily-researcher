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
    "sub_header": {
        "zh": "配置面板 — 编辑 .env 和 configs/config.json",
        "en": "Configuration Panel — Edit .env and configs/config.json",
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
    "tab_usage": {"zh": "用量统计", "en": "Usage"},
    "nav_label": {"zh": "导航", "en": "Navigation"},
    "rm_auto_refresh": {"zh": "自动刷新", "en": "Auto refresh"},
    "rm_auto_refresh_help": {
        "zh": "开启后运行状态与日志尾部每 5 秒自动刷新，无需手动刷新页面。",
        "en": "When on, run status and the log tail refresh every 5 seconds.",
    },
    "rm_live_tail_hint": {
        "zh": "尾部 12 行 · 每 5 秒自动刷新",
        "en": "last 12 lines · auto-refreshes every 5s",
    },
    # ── usage.py ────────────────────────────────────────────────────────
    "usage_title": {"zh": "LLM Token 用量", "en": "LLM Token Usage"},
    "usage_hint": {
        "zh": "统计每次运行结束落库的真实 token 消耗；数据永久保留、永不删除。",
        "en": "Real token usage persisted at the end of every run; kept forever.",
    },
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
    # ── favorites.py ────────────────────────────────────────────────────
    "tab_favorites": {"zh": "收藏偏好", "en": "Favorites"},
    "fav_title": {"zh": "收藏偏好", "en": "Paper Favorites"},
    "fav_hint": {
        "zh": "给论文点喜欢或不喜欢；标记只用于汇总你的兴趣画像（作者、领域、关键词），"
              "不会影响 AI 评分。数据永久保留。",
        "en": "Mark papers you like or dislike. Marks only build your interest "
              "profile (authors, categories, keywords) and never affect AI "
              "scoring. Data is kept forever.",
    },
    "fav_mark_title": {"zh": "论文标注", "en": "Mark papers"},
    "fav_mark_empty": {
        "zh": "暂无可标注的论文——完成一次每日研究后这里会出现论文列表。",
        "en": "No papers to mark yet — complete a daily research run first.",
    },
    "fav_like": {"zh": "喜欢", "en": "Like"},
    "fav_dislike": {"zh": "不喜欢", "en": "Dislike"},
    "fav_clear": {"zh": "清除标记", "en": "Clear mark"},
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
    "fav_load_failed": {
        "zh": "收藏数据读取失败",
        "en": "Failed to load favorites data",
    },
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
    "clear_saved_secret": {"zh": "清除已保存的密钥", "en": "Clear saved secret"},
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
    "run_now_section_title": {"zh": "立即运行每日研究", "en": "Run Daily Research Now"},
    "run_now_btn": {"zh": "立即运行", "en": "Run Now"},
    "run_now_hint": {
        "zh": "立即执行一次每日研究流水线（包含抓取论文、评分、分析和发送通知）。",
        "en": "Immediately run the daily research pipeline (fetch, score, analyze, notify).",
    },
    "stop_all_btn": {"zh": "停止所有进程", "en": "Stop All Processes"},
    "stop_all_hint": {
        "zh": "向所有正在运行的每日研究和趋势分析进程发送停止信号。",
        "en": "Send stop signal to all running daily research and trend analysis processes.",
    },
    "run_log_title": {"zh": "运行日志", "en": "Run Logs"},
    "daily_research_settings_title": {"zh": "每日研究设置", "en": "Daily Research Settings"},
    "daily_max_papers_label": {
        "zh": "本次最多处理论文数",
        "en": "Maximum papers per run",
    },
    "daily_max_papers_help": {
        "zh": "0 表示处理全部。正数只限制本次评分/分析数量；抓取仍完整执行，剩余论文保存在 SQLite 中等待下次处理。",
        "en": "0 processes all papers. A positive value limits scoring/analysis only; scans remain complete and excess papers stay queued in SQLite.",
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
    "rm_status_title": {"zh": "当前运行状态", "en": "Current Run Status"},
    "rm_no_running_tasks": {
        "zh": "当前无正在运行的任务（无锁/PID 文件）。",
        "en": "No running tasks (no lock/PID files).",
    },
    "rm_status_running": {"zh": "运行中", "en": "Running"},
    "rm_status_stopped": {"zh": "已停止（文件未清除）", "en": "Stopped (file not cleaned)"},
    "rm_no_pid": {"zh": "无 PID", "en": "No PID"},
    "rm_started_at": {"zh": "启动于", "en": "Started at"},
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
    "trend_skills_label": {"zh": "启用的分析技能", "en": "Enabled Analysis Skills"},
    "trend_output_formats_label": {"zh": "输出格式", "en": "Output Formats"},
    "trend_output_md_label": {"zh": "Markdown 报告", "en": "Markdown Report"},
    "trend_output_html_label": {"zh": "HTML 报告", "en": "HTML Report"},
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
    "daily_scan_all_results": {
        "zh": "日报会处理时间范围内的全部新论文；系统通过请求限速与重试保护上游服务，不使用篇数预算。",
        "en": "Daily research processes every new paper in the time window. Request pacing and retries protect upstream services; there is no item budget.",
    },
    "data_sources_title": {"zh": "数据源", "en": "Data Sources"},
    "data_sources_hint": {
        "zh": "核心来源只保留 arXiv 和 PRL；其他来源通过安全的声明式 JSON 添加。",
        "en": "Core sources are arXiv and PRL; add other sources with safe declarative JSON.",
    },
    "extra_sources_title": {"zh": "额外来源", "en": "Extra Sources"},
    "extra_sources_enabled": {"zh": "启用额外来源", "en": "Enable extra sources"},
    "extra_sources_help": {
        "zh": "关闭时保留文本内容但不抓取；开启后只接受内置类型，不执行 Python 代码。",
        "en": "When off, definitions are retained but not fetched. Only built-in types are accepted; Python code is never executed.",
    },
    "extra_sources_json_label": {
        "zh": "来源定义（JSON 数组）",
        "en": "Source definitions (JSON array)",
    },
    "extra_sources_json_help": {
        "zh": "OpenAlex 期刊格式：type=openalex_journal、唯一 code、显示名称、全名及 ISSN 数组；也支持 type=huggingface_papers。",
        "en": "OpenAlex journals use type=openalex_journal, a unique code, names and an ISSN array; type=huggingface_papers is also supported.",
    },
    "extra_sources_templates_title": {
        "zh": "可复制的内置来源模板",
        "en": "Copyable built-in source templates",
    },
    "extra_sources_templates_help": {
        "zh": "从下方数组复制需要的对象到上方输入框；只有输入框中的定义会在开关打开时启用。",
        "en": "Copy only the desired objects into the field above. Definitions present there are enabled when the master switch is on.",
    },
    "extra_sources_valid": {"zh": "来源定义格式有效。", "en": "Source definitions are valid."},
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
    "arxiv_domains_title": {"zh": "ArXiv 目标分类", "en": "ArXiv Target Domains"},
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
    "daily_persistence_required": {
        "zh": "SQLite 状态库已作为每日研究的必备基础设施启用",
        "en": "The SQLite state store is required for daily research",
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
    "trend_research_hint": {
        "zh": "--mode trend_research 分析的相关设置。",
        "en": "Settings for the --mode research_trend analysis.",
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
    "skill_temporal_evolution": {
        "zh": "技术演进时间线",
        "en": "Technology Evolution Timeline",
    },
    "skill_hot_topics": {
        "zh": "热点话题聚类",
        "en": "Hot Topics Clustering",
    },
    "skill_key_authors": {
        "zh": "关键研究者分析",
        "en": "Key Researchers Analysis",
    },
    "skill_research_gaps": {
        "zh": "研究空白识别",
        "en": "Research Gap Identification",
    },
    "skill_methodology_trends": {
        "zh": "方法论趋势",
        "en": "Methodology Trends",
    },
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
    "tr_keywords_placeholder": {
        "zh": '例如: quantum error correction "surface code"',
        "en": 'e.g. quantum error correction "surface code"',
    },
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
    "skill_time_evolution": {"zh": "技术演进时间线", "en": "Technology Evolution Timeline"},
    "skill_key_researchers": {"zh": "关键研究者分析", "en": "Key Researchers Analysis"},
    # run_manager 触发文件机制新 key
    "rm_docker_trigger_hint": {
        "zh": "Docker 模式：通过触发文件与主研究容器通信（安全，无需 Docker socket）",
        "en": "Docker mode: communicates with the researcher container via trigger file (no Docker socket needed)",
    },
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
    "rm_scan_receipts_title": {"zh": "扫描覆盖收据", "en": "Scan Coverage Receipts"},
    "rm_scan_receipts_hint": {
        "zh": "每次日报的全来源抓取证据：用于区分真实低产、历史去重与抓取失败。页面只显示状态和计数，不显示原始错误或请求内容。",
        "en": "All-source evidence for each daily scan. It distinguishes a quiet day, history de-duplication, and fetch failure; only statuses and counters are shown, never raw errors or request content.",
    },
    "rm_scan_receipts_empty": {
        "zh": "尚无可显示的扫描收据；下一次启用持久化的日报运行后会出现。",
        "en": "No scan receipt is available yet. It will appear after the next persistent daily run.",
    },
    "rm_scan_receipts_load_error": {
        "zh": "无法读取本地扫描收据",
        "en": "Could not read local scan receipts",
    },
    "rm_scan_receipts_legacy": {
        "zh": "本地数据库尚未包含扫描收据表；下一次升级后的持久化日报运行会建立可观测记录。",
        "en": "The local database does not yet contain scan receipts. A persistent daily run after the upgrade will create observable records.",
    },
    "rm_scan_receipt_run": {"zh": "运行", "en": "Run"},
    "rm_scan_receipt_window": {"zh": "扫描窗口（天）", "en": "Scan window (days)"},
    "rm_scan_receipt_days": {"zh": "天", "en": "days"},
    "rm_scan_receipt_candidates": {"zh": "新候选", "en": "New candidates"},
    "rm_scan_receipt_domains": {"zh": "领域数", "en": "Domains"},
    "rm_scan_receipt_window_range": {"zh": "实际时间范围", "en": "Actual time window"},
    "rm_scan_receipt_run_error": {"zh": "本次运行错误", "en": "Run error"},
    "rm_scan_receipt_domain": {"zh": "领域", "en": "Domain"},
    "rm_scan_receipt_status": {"zh": "状态", "en": "Status"},
    "rm_scan_receipt_source": {"zh": "来源", "en": "Source"},
    "rm_scan_receipt_sources": {"zh": "来源收据", "en": "Source receipts"},
    "rm_scan_receipt_plan": {"zh": "计划来源", "en": "Planned sources"},
    "rm_scan_receipt_scanned_at": {"zh": "扫描时间", "en": "Scanned at"},
    "rm_scan_receipt_detail": {"zh": "证据粒度", "en": "Evidence detail"},
    "rm_scan_receipt_domain_detail": {"zh": "领域/查询级", "en": "Domain/query level"},
    "rm_scan_receipt_source_detail": {"zh": "来源汇总级", "en": "Source summary"},
    "rm_scan_receipt_failed_sources": {
        "zh": "本次运行有 {n} 个来源的收据未成功；不会据此推进完整扫描水位线。",
        "en": "{n} source receipt(s) did not succeed in this run; the full-scan watermark is not advanced from that evidence.",
    },
    "rm_scan_receipt_submitted": {"zh": "提交查询", "en": "Submitted query"},
    "rm_scan_receipt_updated": {"zh": "更新查询", "en": "Updated query"},
    "rm_scan_receipt_new": {"zh": "新候选", "en": "New"},
    "rm_scan_receipt_history_skip": {"zh": "历史跳过", "en": "History skipped"},
    "rm_scan_receipt_dedup": {"zh": "去重", "en": "De-duplicated"},
    "rm_scan_receipts_no_domain_detail": {
        "zh": "该收据没有可用的领域明细。",
        "en": "This receipt has no usable per-domain details.",
    },
    "rm_scan_receipt_raw": {"zh": "查看原始收据", "en": "View raw receipt"},
    "rm_health_title": {"zh": "运行健康摘要", "en": "Operational Health"},
    "rm_health_hint": {
        "zh": "只读汇总最近 10 次运行及此前 20 次基线的评分、扫描和投递状态；不显示论文内容、错误详情、URL 或凭据。",
        "en": "A read-only aggregate of the latest 10 runs and 20-run baseline for scoring, scans, and delivery; no paper content, error details, URLs, or credentials are shown.",
    },
    "rm_health_empty": {
        "zh": "尚无可用于运行健康摘要的本地持久化数据库。",
        "en": "No local persistence database is available for operational health yet.",
    },
    "rm_health_load_error": {
        "zh": "无法读取本地运行健康摘要",
        "en": "Could not read local operational health",
    },
    "rm_health_recent_runs": {"zh": "最近运行（可用/窗口）", "en": "Recent runs (available/window)"},
    "rm_health_run_states": {"zh": "完成/失败", "en": "Completed/failed"},
    "rm_health_qualification_rate": {"zh": "评分通过率", "en": "Qualification rate"},
    "rm_health_notification_backlog": {"zh": "待处理通知", "en": "Open notifications"},
    "rm_health_source_evidence": {"zh": "最近窗口的来源扫描证据", "en": "Source scan evidence in the recent window"},
    "rm_health_source_success": {"zh": "成功收据", "en": "Succeeded receipts"},
    "rm_health_source_failed": {"zh": "失败收据", "en": "Failed receipts"},
    "rm_health_source_missing": {"zh": "缺失收据", "en": "Missing receipts"},
    "rm_health_source_candidates": {"zh": "新候选总数", "en": "New candidates"},
    "rm_health_source_retries": {"zh": "重试查询", "en": "Retried queries"},
    "rm_health_anomaly_hint": {
        "zh": "最近窗口发现需要检查的扫描证据：",
        "en": "Scan evidence needing attention in the recent window:",
    },
    "rm_health_anomaly_kind": {"zh": "观察项", "en": "Observation"},
    "rm_health_anomaly_count": {"zh": "数量", "en": "Count"},
    "rm_health_stage_evidence": {"zh": "论文阶段状态（当前可恢复账本）", "en": "Paper stage state (current resumable ledger)"},
    "rm_health_stage": {"zh": "阶段", "en": "Stage"},
    "rm_health_stage_succeeded": {"zh": "成功", "en": "Succeeded"},
    "rm_health_stage_failed": {"zh": "失败", "en": "Failed"},
    "rm_health_stage_pending": {"zh": "待处理", "en": "Pending"},
    "rm_health_outbox_evidence": {"zh": "投递队列健康", "en": "Delivery queue health"},
    "rm_health_outbox": {"zh": "队列", "en": "Queue"},
    "rm_health_outbox_available": {"zh": "可用", "en": "Available"},
    "rm_health_outbox_open": {"zh": "未完成", "en": "Open"},
    "rm_health_outbox_retrying": {"zh": "重试中", "en": "Retrying"},
    "rm_health_outbox_attempts": {"zh": "最大尝试数", "en": "Max attempts"},
    "rm_health_yes": {"zh": "是", "en": "Yes"},
    "rm_health_no": {"zh": "否", "en": "No"},
    "rm_advanced_diagnostics": {
        "zh": "高级诊断：扫描覆盖与运行健康",
        "en": "Advanced diagnostics: scan coverage and operational health",
    },
    # ── proxy.py ──────────────────────────────────────────────────────
    "tab_proxy": {"zh": "网络代理", "en": "Network Proxy"},
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
}


def t(key: str) -> str:
    """Return the translated string for the current language."""
    lang = st.session_state.get("lang", "zh")
    entry = _TRANSLATIONS.get(key, {})
    return entry.get(lang, entry.get("en", key))
