# Reddit r/MachineLearning 推广帖

## 标题

**[P] ArXiv Daily Researcher — Open-source LLM-powered paper monitoring with deep PDF analysis, trend research, and Docker one-click deploy**

## 正文

I built an open-source tool to automate the daily paper grind.

**What it does:**
- Fetches papers from ArXiv + 20+ journals (PRL, Nature, Science, etc.) daily
- Uses a cheap LLM (GPT-4o-mini / Claude Haiku) to score each paper against your keyword weights
- Downloads matched PDFs and runs deep analysis via a strong LLM (GPT-4o / Claude Opus) — extracts methodology, novelty, tech stack, key findings, limitations, related work, and future directions
- Generates Markdown + HTML reports, pushes notifications (Telegram, Email, Slack, etc.)
- Tracks token costs per run so you know exactly what you're spending

**What makes it different from ArXiv RSS/Google Scholar alerts:**
- LLM-powered relevance scoring, not just keyword matching
- PDF-level deep analysis for the ~10% of papers that pass the filter
- Trend research mode: one command analyzes a topic across a full year
- Docker one-click deploy with auto setup wizard
- Streamlit WebUI for all config (no editing config files)

**Cost:** ~$0.1-0.5 per daily run with GPT-4o-mini as cheap LLM. Cheaper with local models (Ollama supported).

**Recent updates (v3.0 → v3.2):**
- Network proxy system (HTTP/SOCKS5, per-service granularity)
- WebDAV sync (multi-device config sharing)
- GitHub Actions support (fully free cloud runs)
- Token cost tracking per model
- i18n (Chinese/English)
- Concurrent run mutex locks

**Links:**
- GitHub: https://github.com/yzr278892/arxiv-daily-researcher
- License: AGPL-3.0

Happy to answer questions. Would love feedback from the ML community on the LLM scoring approach.

---

## 发布注意事项

1. **Subreddit 规则**：r/ML 要求 [P] (Project) 标签，必须 self-post 格式
2. **时间**：EST 上午 9-11 点（对应北京时间晚上 9-11 点）
3. **互动**：及时回复评论，展示项目活跃度
4. **交叉发布**：也可以发到 r/arxiv, r/academicpublishing, r/Python
