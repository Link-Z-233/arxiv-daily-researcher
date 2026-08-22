<div align="center">

# 🔬 ArXiv Daily Researcher

**LLM-Powered Intelligent Academic Paper Monitoring, Filtering, Deep Analysis & Trend Research System**

[![Version](https://img.shields.io/badge/version-4.0-brightgreen.svg)](CHANGELOG.md)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?logo=docker&logoColor=white)](https://www.docker.com/)
[![GitHub Actions](https://img.shields.io/badge/GitHub_Actions-Supported-2088FF?logo=github-actions&logoColor=white)](https://github.com/features/actions)
[![Streamlit](https://img.shields.io/badge/Config_Panel-Streamlit-FF4B4B?logo=streamlit&logoColor=white)](#️-streamlit-config-panel)
[![中文](https://img.shields.io/badge/README-中文-red.svg)](README.md)

*Daily high-quality paper summaries; one command to survey a year of research trends; one panel for config, execution, preview, and debugging.*

</div>

---

ArXiv Daily Researcher automatically fetches papers from **ArXiv and 20+ academic journals**, filters relevant work using a configurable keyword-weight scoring system, downloads PDFs for deep analysis, tracks keyword evolution trends, generates Markdown/HTML reports, and pushes results to multiple notification channels.

The current version supports:
- **Daily Research Mode** — ongoing monitoring of new papers matching your keywords
- **Trend Research Mode** — long-term trend insights for specified topics
- **Streamlit WebUI Panel** — browser-based config, one-click execution, log viewing, and report preview

---

## ✨ Core Features

<table>
<tr>
<td colspan="2" align="center"><sub>— Data Acquisition & Intelligent Filtering —</sub></td>
</tr>
<tr>
<td width="50%" valign="top">

### 📡 Multi-Source Fetching

Supports **ArXiv and 20+ top journals** (PRL, Nature, Science, etc.). When a journal paper has an ArXiv version, the system automatically switches to ArXiv for more complete abstracts and downloadable PDFs. Optional integration with **Semantic Scholar** for citation counts and AI TLDRs.

</td>
<td width="50%" valign="top">

### 🎯 Dual LLM Scoring & Filtering

`CHEAP_LLM` scores each paper against your keywords on a 0–10 scale. Papers are promoted to deep analysis based on **weighted keyword sums** and a **dynamic pass threshold**. Supports primary keywords, auto-extracted keywords from reference PDFs, and expert author bonuses.

</td>
</tr>
<tr>
<td colspan="2" align="center"><sub>— Deep Analysis & Knowledge Accumulation —</sub></td>
</tr>
<tr>
<td width="50%" valign="top">

### 🔍 Deep PDF Analysis

Papers that pass the scoring filter are automatically downloaded, and `SMART_LLM` extracts **methodology, novelty, tech stack, key findings, limitations, research connections, and future directions** — seven dimensions in total. Supports both **MinerU cloud parsing** and **PyMuPDF local parsing**, with automatic fallback when MinerU is unavailable.

</td>
<td width="50%" valign="top">

### 📈 Keyword Trend Tracking

Keywords extracted during scoring are written to SQLite, then semantically merged and normalized via AI. The system generates Mermaid charts and standalone HTML keyword trend reports with **color-coded bar charts, trend heatmaps, and unified color legends**.

</td>
</tr>
<tr>
<td colspan="2" align="center"><sub>— Trend Research & Cost Observability —</sub></td>
</tr>
<tr>
<td width="50%" valign="top">

### 🔬 Trend Research Mode

The standalone `trend_research` mode supports keyword-based, date-range-filtered, ArXiv-category-scoped batch paper retrieval. Each paper gets a TLDR, and `SMART_LLM` performs a **single-pass comprehensive analysis** covering hot topics, temporal evolution, key researchers, research gaps, and methodology trends.

</td>
<td width="50%" valign="top">

### 📊 Token Consumption Tracking

A built-in thread-safe token counter tracks input/output token consumption per model for every run, displayed at the **end of reports** and in **notification messages**, giving you precise visibility into running costs.

</td>
</tr>
<tr>
<td colspan="2" align="center"><sub>— Report Output & Notification Delivery —</sub></td>
</tr>
<tr>
<td width="50%" valign="top">

### 📄 Dual-Format Reports (Markdown + HTML)

Supports three report types: **Daily Research**, **Trend Research**, and **Keyword Trends**. Markdown is ideal for archiving and version control (independently toggleable); HTML is optimized for browser reading and sharing, with external CSS styling and integrated **KaTeX** formula rendering.

</td>
<td width="50%" valign="top">

### 🔔 Six-Channel Notifications

Supports **Email, WeCom, DingTalk, Telegram, Slack, and generic Webhook**. Each channel has an independent enable switch. Email supports HTML templates. Real-time alerts for runtime anomalies (MinerU expiration, LLM errors, network issues).

</td>
</tr>
<tr>
<td colspan="2" align="center"><sub>— Config Management & Deployment —</sub></td>
</tr>
<tr>
<td width="50%" valign="top">

### 🧙 Interactive Setup Wizard

First-time deployment includes a 7-step CLI wizard covering LLM, search, data sources, keywords, scoring, notifications, and advanced settings. **Auto-triggered** on first Docker deployment, automatically generating `.env` and `configs/config.json`.

</td>
<td width="50%" valign="top">

### 🖥️ Streamlit Config Panel <sup><kbd>v4.0</kbd></sup>

An **11-tab** browser-based management interface: Daily Push (run manager + report toggles), Report Viewer, Trend Analysis, Keywords, Search, Scoring, Notifications, Data Management (config export + WebDAV sync), API Config, Network Proxy, and Advanced Settings.

</td>
</tr>
<tr>
<td width="50%" valign="top">

### 🚀 Three Deployment Modes

Supports **Docker containers** (recommended), **local scripts + Cron**, and **GitHub Actions**. Docker is the preferred approach — zero-config, production-ready.

</td>
<td width="50%" valign="top">

### 🛡️ Production-Grade Reliability

Built-in **exponential backoff retry**, **MinerU smart degradation**, **file-lock-based concurrency prevention**, **stale lock reclamation**, **per-run log files**, **auto update checks**, **network proxy**, and **WebDAV cross-device data sync** — suitable for long-term unattended operation.

</td>
</tr>
</table>

---

## 📑 Navigation

<table>
<tr>
<td width="50%" valign="top">

### 📘 Getting Started

| Section | Description |
| :------------------------: | :-------------------------- |
| [✨ Core Features](#-core-features) | Feature overview |
| [🚀 Quick Start](#-quick-start) | Three steps to first run |
| [🛠️ Config Tools](#️-config-tools) | CLI wizard + Streamlit panel |
| [🐳 Deployment](#-deployment) | Docker / Actions / local cron |

</td>
<td width="50%" valign="top">

### 📗 In Depth

| Section | Description |
| :------------------------: | :--------------------------- |
| [📖 Feature Details](#-feature-details) | Modes, reports, notifications, locking |
| [📁 Project Structure](#-project-structure) | Directory and module overview |
| [❓ FAQ](#-faq) | 10 practical troubleshooting & deep-dive guides |
| [📝 Changelog](CHANGELOG.md) | Complete version history |

</td>
</tr>
</table>

---

## 🚀 Quick Start

### Step 1: Clone & Install

```bash
git clone https://github.com/yzr278892/arxiv-daily-researcher.git
cd arxiv-daily-researcher
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### Step 2: Configure

We recommend running the interactive setup wizard first:

```bash
python src/utils/setup_wizard.py
```

The wizard guides you through:

- LLM Configuration
- Search Parameters
- Data Source Selection
- Keywords & Research Context
- Scoring Parameters
- Notification Channels
- Advanced Settings

Upon completion, the following are auto-generated:
- `.env`
- `configs/config.json`

> [!TIP]
> If configs already exist, the wizard pre-fills existing values; just modify the fields you want to change and press Enter to keep the rest.

<details>
<summary><b>Manual Configuration (skip wizard)</b></summary>

**1) Copy environment template:**

```bash
cp .env.example .env
```

**2) Configure LLM:**

```env
CHEAP_LLM__API_KEY=sk-your-key
CHEAP_LLM__BASE_URL=https://api.openai.com/v1
CHEAP_LLM__MODEL_NAME=gpt-4o-mini

SMART_LLM__API_KEY=sk-your-key
SMART_LLM__BASE_URL=https://api.openai.com/v1
SMART_LLM__MODEL_NAME=gpt-4o
```

**3) Configure core keywords and domains:**

```jsonc
{
  "keywords": {
    "primary_keywords": {
      "weight": 1.0,
      "keywords": ["quantum error correction", "surface code"]
    },
    "research_context": "My research focuses on fault-tolerant quantum computing and quantum error correction codes"
  },
  "target_domains": {
    "domains": ["quant-ph"]
  }
}
```

</details>

### Step 3: Run

```bash
# Daily research mode (default)
python main.py

# Trend research mode
python main.py --mode trend_research --keywords "quantum error correction"
```

Default output locations:
- Reports: `data/reports/`
- Logs: `logs/`

---

## 🛠️ Config Tools

This project offers two main configuration methods: **CLI Setup Wizard** and **Streamlit Config Panel**.

### 🧙 Interactive Setup Wizard

Ideal for first-time deployment, SSH environments, and headless servers:

```bash
python src/utils/setup_wizard.py
```

| Step | Content | Description |
| :---: | :------- | :---------------------------------------- |
| 1 | LLM Config | Choose provider, enter API key, optional connection test |
| 2 | Search Settings | Search days, max results per source |
| 3 | Data Sources | ArXiv & journal toggles, ArXiv categories |
| 4 | Keywords | Primary keywords, reference PDF extraction, research context |
| 5 | Scoring | Base score, weight coefficient, author bonus |
| 6 | Notifications | Channel toggles and credential entry |
| 7 | Advanced | PDF parsing, concurrency, log retention, etc. |

The wizard automatically backs up existing configs to `.bak` files before writing.

---

### 🖥️ Streamlit Config Panel

#### Launch

```bash
# Local
streamlit run src/webui/config_panel.py
```

```bash
# Docker
docker compose up -d config-panel
```

Open in browser: `http://localhost:8501`

The config panel shares the same `.env` and `configs/config.json` as the main program. Changes take effect on the next run.

#### 11 Tabs Overview

| # | Tab | Functionality |
| :---: | :----------- | :----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1 | **Daily Push** | One-click daily research run; run status monitoring (lock file / PID); **Daily Research Settings** (HTML report / Markdown report / Include all papers); run log viewer with auto-redirect to latest non-system log |
| 2 | **Report Viewer** | Three-column display of Daily Research / Trend Research / Keyword Trend HTML reports; auto-opens latest visible report; report preview, trend metadata display, same-source date navigation |
| 3 | **Trend Analysis** | Set keywords, date range, category filter, sort order, max results, TLDR, Markdown/HTML dual-toggle output format, and Skill selection; one-click start/stop trend research |
| 4 | **Keywords** | Manage primary keywords, reference PDF extraction, similarity threshold, weight distribution, research context |
| 5 | **Search** | Search days, per-source fetch count, data source toggles, ArXiv categories and fetch timeout |
| 6 | **Scoring** | Pass threshold formula, max score per keyword, author bonus, live scoring preview |
| 7 | **Notifications** | Global toggle, success/failure/attachment controls, six-channel config, SMTP test |
| 8 | **Data Management** | One-click config export (config.json + .env) as zip; **WebDAV sync** (manual / scheduled / post-report auto), with connection test, upload, download |
| 9 | **API** | Configure CHEAP_LLM / SMART_LLM / MinerU, with connection test support |
| 10 | **Network Proxy** | HTTP/SOCKS5 proxy config with per-service granularity (ArXiv / OpenAlex / Semantic Scholar / LLM API / Notifications / Update Check), Docker & GitHub Actions compatible |
| 11 | **Advanced** | PDF parsing mode, concurrency, token tracking, auto update check, keyword trend tracking, retry, log rotation, and stale lock reclamation |

### 🖼️ WebUI Screenshots

<table>
  <tr>
    <td align="center" width="50%">
      <img src="assets/img_en.png" alt="English WebUI" width="100%" />
      <br />
      <sub>English WebUI</sub>
    </td>
    <td align="center" width="50%">
      <img src="assets/img_noti.png" alt="Notification settings" width="100%" />
      <br />
      <sub>Chinese Notification Settings</sub>
    </td>
  </tr>
  <tr>
    <td align="center" width="50%">
      <img src="assets/img_prev.png" alt="Report preview" width="100%" />
      <br />
      <sub>Chinese Report Preview</sub>
    </td>
    <td align="center" width="50%">
      <img src="assets/img_serh.png" alt="Search sources settings" width="100%" />
      <br />
      <sub>Chinese Search Source Settings</sub>
    </td>
  </tr>
</table>

<details>
<summary><b>Setup Wizard vs. Config Panel — which should I use?</b></summary>

| Tool | Best For | Characteristics |
| :------------------------------- | :-------------------------- | :-------------------------------------------- |
| **Setup Wizard** (`setup_wizard.py`) | First deployment, SSH, headless environments | CLI interactive, ideal for initialization, connection testing |
| **Config Panel** (`config_panel.py`) | Daily tuning, report preview, debugging | 11 tabs, WYSIWYG, run management & trend analysis |

**Recommendation**: Use the wizard for initial setup, then the config panel for ongoing daily use.

</details>

---

## 🐳 Deployment

### Docker Deployment <sup>Recommended</sup>

Docker is the **recommended deployment method** for long-term background operation. The main research container uses `network_mode: host` by default for direct access to the host's local LLM services.

#### Starting Up

```bash
git clone https://github.com/yzr278892/arxiv-daily-researcher.git
cd arxiv-daily-researcher
cp .env.example .env
docker compose up -d
```

Default container behavior:
- `CRON_SCHEDULE=0 8 * * *`
- `RUN_ON_STARTUP=false`
- `MODE=cron`
- `SETUP_WIZARD=auto`

This means, by default:
1. On first deployment, automatically checks whether to launch the setup wizard
2. Starts both the main research service and WebUI config panel (`http://localhost:8501`)
3. Main research container does not run immediately on startup (set `RUN_ON_STARTUP=true` if needed)
4. Automatically runs daily at 08:00 thereafter

#### Common Commands

```bash
# Check running status
docker compose ps

# View logs
docker compose logs -f

# Start / stop WebUI
docker compose up -d config-panel
docker compose stop config-panel

# Run trend research directly inside the container
docker exec -it arxiv-daily-researcher python main.py --mode trend_research \
  --keywords "quantum error correction" \
  --date-from 2025-01-01 \
  --categories quant-ph

# Stop the main service
docker compose down
```

#### WebUI Trigger Mechanism

The WebUI triggers the main container to execute tasks via shared volume trigger files:
- WebUI writes: `data/run/webui_run_trigger.flag`
- Main container's `trigger_watcher` polls every 5 seconds in `entrypoint.sh`
- On detection, launches `python main.py --mode daily_research`
- Run logs go to `logs/manual_*.log`
- The actual Python PID is written to `data/run/webui_triggered.pid`

<details>
<summary><b>Container Environment Variables</b></summary>

| Variable | Default | Description |
| :--------------- | :-------------- | :------------------------------------------------- |
| `TZ` | `Asia/Shanghai` | Timezone |
| `CRON_SCHEDULE` | `0 8 * * *` | Daily scheduled execution time |
| `RUN_ON_STARTUP` | `false` | Whether to run once immediately on startup |
| `MODE` | `cron` | `cron` for scheduled mode, `run-once` for single execution |
| `SETUP_WIZARD` | `auto` | `auto` triggers on first deploy, `true` forces trigger, `false` skips |

</details>

<details>
<summary><b>Using Local LLMs (Ollama, etc.)</b></summary>

Since the main research container uses `network_mode: host`, it can directly access local LLM services on the host:

```env
CHEAP_LLM__API_KEY=ollama
CHEAP_LLM__BASE_URL=http://127.0.0.1:11434/v1
CHEAP_LLM__MODEL_NAME=qwen2.5:7b
```

</details>

---

### GitHub Actions (Cloud)

Ideal for scenarios without a dedicated server. Two workflows are provided:
- `daily-run.yml`: Daily research
- `trend-research.yml`: Manual trend research

> [!IMPORTANT]
> **Usage note**: GitHub Actions is suitable for basic usage or testing. Please follow GitHub's usage policies and do not abuse Actions resources. The `schedule:` trigger in `daily-run.yml` is **commented out by default** — enable it only after configuring Secrets. **For long-term production use, Docker deployment is recommended**.

#### Setup Steps

1. Fork this repository
2. Go to **Settings → Secrets and variables → Actions**
3. Configure at least the following Secrets:

| Secret Name | Required | Description |
| :--------------------- | :---: | :--------------------------- |
| `CHEAP_LLM_API_KEY` | ✅ | Cheap LLM API key |
| `CHEAP_LLM_BASE_URL` | ✅ | Cheap LLM API base URL |
| `CHEAP_LLM_MODEL_NAME` | ✅ | Cheap LLM model name |
| `SMART_LLM_API_KEY` | ✅ | Smart LLM API key |
| `SMART_LLM_BASE_URL` | ✅ | Smart LLM API base URL |
| `SMART_LLM_MODEL_NAME` | ✅ | Smart LLM model name |
| Notification Secrets | Optional | SMTP / Telegram / Webhook etc. |

> [!NOTE]
> The `schedule:` block in `daily-run.yml` is commented out by default. Fork first, configure Secrets, then uncomment the schedule trigger to avoid failed runs from empty configs.

#### Manual Trend Research

`trend-research.yml` accepts:
- `keywords`
- `date_from`
- `date_to`
- `categories`
- `sort_order`
- `max_results`

Reports are saved as Artifacts with 30-day retention.

---

### Local Cron (System Scheduler)

If you prefer not to use Docker or GitHub Actions, you can use system cron directly:

```bash
crontab -e
0 8 * * * cd /path/to/arxiv-daily-researcher && ./scripts/run_daily.sh >> /tmp/arxiv-cron.log 2>&1
```

---

## 📖 Feature Details

### 🔄 Two Run Modes

| Dimension | `daily_research` (default) | `trend_research` |
| :------- | :----------------------------- | :----------------------------- |
| Purpose | Daily tracking of latest papers | Long-term trend analysis by topic |
| Data Sources | ArXiv + journals | ArXiv |
| Time Range | Last N days | Arbitrary date range |
| Filtering | Keyword-weighted scoring | No scoring, keep all |
| Core Analysis | Deep PDF analysis of top papers | TLDR for all + comprehensive trend analysis |
| Trigger | Cron / Docker / Actions / Panel | CLI / Panel / Actions |
| Output Path | `data/reports/daily_research/` | `data/reports/trend_research/` |

### 📅 Daily Research Pipeline

```text
1. Prepare keywords and dynamic pass threshold
2. Fetch papers from ArXiv / journals
3. Skip historically processed papers
4. Score each paper by keywords using CHEAP_LLM
5. Extract and track paper keywords
6. Perform PDF deep analysis on papers that pass the filter
7. Generate Markdown report (independently toggleable) / HTML report (independently toggleable)
8. Send notifications
```

### 🔬 Trend Research Pipeline

```text
1. Search ArXiv by keywords, date, and category
2. Generate TLDR for each paper
3. SMART_LLM comprehensive analysis across five dimensions
4. Output Markdown / HTML reports (dual-toggle, independently controlled) / metadata.json
5. Push trend analysis notification
```

### 🎯 Dynamic Pass Threshold Formula

The default configuration uses:

```text
threshold = base_score + weight_coefficient × Σ(keyword weights)
```

In the default `configs/config.json`:
- `base_score = 1.5`
- `weight_coefficient = 2.5`

You can freely adjust these in the Daily Push tab's "Daily Research Settings" or directly in `configs/config.json`.

### 📡 Data Sources & ArXiv-First Strategy

- ArXiv: uses the official `arxiv` Python library
- Journals: fetches latest papers via OpenAlex
- If a journal paper has an ArXiv version, the system preferentially switches to ArXiv metadata and PDF
- Optional Semantic Scholar integration for citation counts and AI TLDRs

### 🔍 PDF Parsing & Smart Degradation

Two parsing modes are supported:

| Mode | Strengths | Limitations |
| :-------- | :--------------------------- | :------------------ |
| `mineru` | Better structure extraction, handles complex layouts | Requires token |
| `pymupdf` | Pure local, zero external dependencies | Parse quality varies by PDF |

When MinerU is unavailable, the system automatically degrades to PyMuPDF, preventing entire task failure.

### 📈 Keyword Trend Tracking

The keyword tracking module:
- Writes raw keywords to SQLite
- Normalizes keywords in batches via AI
- Generates frequency statistics and trend charts
- Outputs standalone HTML keyword trend reports

Common configuration options:
- `keyword_tracker.enabled`
- `keyword_normalization_enabled`
- `keyword_normalization_batch_size`
- `keyword_report_frequency`

### 🔒 Concurrent Run Mutex Locks

To prevent duplicate runs, the system uses `fcntl` file locks:

| Mode | Lock File |
| :--------------- | :------------------------------------- |
| `daily_research` | `data/run/daily_research.lock` |
| `trend_research` | `data/run/trend_research_<hash8>.lock` |

Features:
- Duplicate task launches exit safely
- Lock files contain PID and start time
- Supports **stale lock reclamation** (default 12 hours)
- Conservatively exits on reclamation failure to prevent dual-instance concurrency

### ⏱️ ArXiv Fetch Timeout Guard

ArXiv fetching includes hard timeout protection:
- Config key: `data_sources.arxiv.fetch_timeout_seconds`
- Current default: `180`
- Per-domain timeout triggers retry with logging

### 📄 Report System

#### Daily Research Reports

Paths:
- `data/reports/daily_research/markdown/<source>/`
- `data/reports/daily_research/html/<source>/`

Markdown and HTML reports can be **independently toggled** (configured in Daily Push → Daily Research Settings).

Content typically includes:
- Statistics summary
- Details of passed papers
- List of non-passed papers
- Deep analysis results
- Keyword trend charts
- Token consumption statistics

#### Trend Research Reports

Paths:
- `data/reports/trend_research/markdown/<keyword_slug>/`
- `data/reports/trend_research/html/<keyword_slug>/`

Also generates:
- `*_metadata.json`

#### Keyword Trend Reports

Paths:
- `data/reports/keyword_trend/markdown/`
- `data/reports/keyword_trend/html/`

### 🔔 Notification System

Six supported channels:
- Email
- WeCom
- DingTalk
- Telegram
- Slack
- Generic Webhook

Notification toggles operate at two levels:
1. Global notification master switch
2. Per-channel independent switches

A channel only sends notifications when **configured credentials are present** AND **enabled=true**.

---

## 📁 Project Structure

```text
arxiv-daily-researcher/
├── main.py                          # CLI entry point, dispatches by mode
├── .env.example                     # Environment variable template
├── requirements.txt                 # Python dependencies
├── README.md                        # Chinese README (this file is README_EN.md)
│
├── src/
│   ├── config.py                    # Global config loading
│   ├── modes/                       # Two run modes
│   │   ├── daily_research.py
│   │   └── trend_research.py
│   ├── agents/                      # LLM analysis agents
│   ├── sources/                     # ArXiv / OpenAlex / search orchestration
│   ├── report/                      # Daily / trend / keyword trend report generation
│   ├── notifications/               # Multi-channel notifications
│   ├── parsers/                     # PDF parsing
│   ├── keyword_tracker/             # Keyword tracking & normalization
│   ├── utils/                       # Config, logging, locks, tokens, wizard, WebDAV, etc.
│   │   ├── config_io.py
│   │   ├── updater.py
│   │   └── webdav_sync.py           # WebDAV sync module
│   └── webui/                       # Streamlit config panel
│       ├── config_panel.py
│       ├── i18n.py
│       └── tabs/
│           ├── run_manager.py       # Daily Push
│           ├── reports.py           # Report Viewer
│           ├── trend_runner.py      # Trend Analysis
│           ├── keywords.py
│           ├── search.py
│           ├── scoring.py
│           ├── notifications.py
│           ├── data_management.py   # Config export + WebDAV sync
│           ├── proxy.py             # Network proxy
│           ├── llm.py
│           └── advanced.py
│
├── configs/
│   ├── config.json                  # Main config file (JSONC)
│   └── templates/                   # Report, notification, and email templates
│
├── docker-compose.yml               # Docker Compose orchestration file
├── docker/
│   ├── Dockerfile
│   │   └── (targets: worker / webui)
│   └── entrypoint.sh
│
├── VERSION                          # Version number (for Docker update check)
├── scripts/                         # Run scripts & Makefile
├── assets/                          # README / WebUI preview images
├── data/                            # Runtime data (auto-created)
└── logs/                            # System logs and per-run logs
```

---

## ❓ FAQ

<details>
<summary><b>1. WebDAV connection to Jianguoyun (Nutstore) always fails (403)?</b></summary>

Jianguoyun's WebDAV server **does not support HTTP HEAD requests**, and most WebDAV client libraries use HEAD for resource existence checks. This project has built-in compatibility handling (using PROPFIND instead of HEAD).

If you still encounter connection issues, check:
- WebDAV URL ends with `https://dav.jianguoyun.com/dav/`
- Password is a Jianguoyun **app-specific password** (generated in account security settings, not your login password)
- Click "Test Connection" in the WebUI Data Management tab to verify credentials
</details>

<details>
<summary><b>2. How to choose the right parameters for trend analysis?</b></summary>

Key recommendations:
- **Date range**: Start with 90-180 days (`--date-from`) to avoid excessive results
- **Category filter**: Use `--categories` to scope to relevant fields (e.g., `quant-ph cond-mat`) for much better precision
- **Output format**: Markdown and HTML are independently toggleable in the Trend Analysis tab
- **Skill selection**: Default `comprehensive_analysis` covers all five dimensions in one pass; enable individual Skills for single-dimension deep dives
- **max_results**: Default 500. Lower to 200 if analysis is slow with many results; raise to 1000 if you need more coverage
</details>

<details>
<summary><b>3. The task says "already running" but I suspect a stale lock?</b></summary>

The system provides multiple layers of protection:
- **Dead process lock auto-cleanup**: On startup, checks if PID is alive; auto-reclaims if not
- **Stale lock auto-reclamation**: Locks older than `run_lock_max_age_hours` (default 12 hours) are reclaimed
- **Manual cleanup**: In the WebUI Daily Push tab under "Current Run Status", click the stop/clean button; or delete `data/run/*.lock` files directly

> [!WARNING]
> Only manually clean locks when you are certain the PID is no longer alive. If the process is genuinely running, removing the lock may cause duplicate execution.
</details>

<details>
<summary><b>4. How to configure and use local LLMs (Ollama / vLLM / LocalAI) in Docker?</b></summary>

The main research container uses `network_mode: host` by default, so it can directly access local LLM services on the host:

```env
CHEAP_LLM__API_KEY=ollama
CHEAP_LLM__BASE_URL=http://127.0.0.1:11434/v1
CHEAP_LLM__MODEL_NAME=qwen2.5:7b
```

If using bridge network mode (WebUI container, etc.), replace `127.0.0.1` with `host.docker.internal` (Windows/Mac) or the host's real IP (Linux).

Ensure your local LLM service is listening on `0.0.0.0` rather than `127.0.0.1`, otherwise containers cannot reach it.
</details>

<details>
<summary><b>5. How does the WebUI "Run Now" button work with the main container?</b></summary>

Docker mode uses a **file-trigger mechanism** — no Docker socket required:

1. User clicks "Run Now" in WebUI
2. WebUI container writes a trigger file to the shared volume: `data/run/webui_run_trigger.flag`
3. The main container's `trigger_watcher` polls this file every 5 seconds
4. On detection, the main container launches `python main.py --mode daily_research`
5. The actual Python PID is written to `data/run/webui_triggered.pid`
6. Run logs go to `logs/manual_*.log`, viewable in real-time from WebUI

The key requirement: both containers must mount the **same** `data/` and `logs/` volumes.
</details>

<details>
<summary><b>6. How to configure network proxy? Can proxy be controlled per-service?</b></summary>

Configure in the WebUI Network Proxy tab or `configs/config.json` under the `proxy` block:

- **Global switch**: `proxy.enabled`
- **Proxy URL**: `proxy.url`, supports HTTP/SOCKS5 (e.g., `http://127.0.0.1:7890`)
- **Per-service control** (`proxy.scope`): Independently control whether ArXiv, OpenAlex, Semantic Scholar, LLM API, notifications, and update checks use the proxy

Docker notes:
- With `network_mode: host`, use `127.0.0.1`
- With bridge mode on Linux, use `--add-host=host.docker.internal:host-gateway`
</details>

<details>
<summary><b>7. What's the difference between Markdown and HTML reports? Can I generate only one format?</b></summary>

Same content, different format:
- **Markdown**: Ideal for Git version control, archiving, plain-text editing
- **HTML**: Ideal for browser reading, sharing, with styling and KaTeX formula rendering

In the WebUI Daily Push → Daily Research Settings, Markdown and HTML report generation can be independently toggled. Trend analysis reports also have independent Markdown/HTML toggles (in the Trend Analysis tab). Disabling unneeded formats saves storage space and generation time.
</details>

<details>
<summary><b>8. How does keyword tracking work? Can it be disabled?</b></summary>

Keyword tracking flow:
1. CHEAP_LLM automatically extracts keywords from paper titles and abstracts during scoring
2. Extracted keywords are written to a SQLite database
3. AI performs batch semantic merging on raw keywords (e.g., "quantum computing" and "quantum computation" are merged)
4. Keyword trend reports are generated periodically (frequency configurable in WebUI: daily/weekly/monthly/always)

Set `keyword_tracker.enabled` to `false` in the WebUI Advanced tab or `config.json` to disable tracking. Scoring results will still show keywords, but they won't be stored in the database or generate trend reports.
</details>

<details>
<summary><b>9. How to choose between MinerU and PyMuPDF for PDF parsing? What if the MinerU token expires?</b></summary>

| Scenario | Recommended |
| :----------------------------- | :-------- |
| Pursuing parse quality, complex layout papers | `mineru` |
| Offline environment, long-term stability | `pymupdf` |

Switch in the WebUI API tab or Advanced tab.

MinerU tokens are valid for 3 months. Upon expiration:
- The system **automatically degrades** to PyMuPDF — no interruption
- An error alert notification is sent (if notification channels are configured)
- Apply for a new token at [mineru.net](https://mineru.net/apiManage/apiKey) and update the config
</details>

<details>
<summary><b>10. How to use WebDAV sync to share configs and reports across multiple devices?</b></summary>

WebDAV sync supports three modes (configured in WebUI Data Management tab):

| Mode | Description |
| :------------- | :------------------------------------- |
| **Manual** | Click "Upload" or "Download" buttons in WebUI |
| **Scheduled** | Auto-execute on a cron schedule (e.g., daily at 23:00) |
| **Post-Report Auto** | Auto-upload after each daily research report is generated |

Sync scope options: config files (config.json), history, keyword data, report files. Default: config files only.

Typical usage: Primary device set to "Post-Report Auto" upload; secondary device set to "Manual" mode, download on demand to restore configs and data.
</details>

---

## 📜 License

This project is licensed under [AGPL-3.0](https://www.gnu.org/licenses/agpl-3.0.html).

| Term | Description |
| :--------- | :--------------------------------------- |
| ✅ Use | Free to use, modify, and distribute |
| ✅ Commercial | Commercial use allowed |
| 📋 Source Disclosure | Modified versions must disclose source code under the same license |
| 🌐 Network Use | Providing the service over a network also requires source disclosure |
| 📝 Attribution | Original copyright notice and license must be retained |

---

## 💬 Community & Feedback

The project is under active development. You're welcome to participate via:

- **🐛 Report Issues**: [GitHub Issues](https://github.com/yzr278892/arxiv-daily-researcher/issues) — Found a bug or have a feature suggestion? Submit an issue
- **🔀 Contribute**: Fork → Modify → Pull Request. All improvements are welcome
- **⭐ Star**: If this project helps you, starring it is the greatest encouragement

---

## 🤝 API Compliance

This project follows the usage policies of all integrated APIs:

| API | Compliance Measures |
| :------------------- | :---------------------------------------------------------------------- |
| **ArXiv** | Uses official `arxiv` Python library, built-in 6-second request delay |
| **OpenAlex** | Request headers include contact info; configuring `OPENALEX_EMAIL` enters the Polite Pool |
| **Semantic Scholar** | Request headers include User-Agent; supports API key for higher rate limits |
| **MinerU** | Respects daily 2000-page priority quota, auto-downgrades to normal priority when exceeded |

> [!NOTE]
> All external API calls include exponential backoff retry mechanisms — network fluctuations won't interrupt runs.

---

## 🙏 Acknowledgments

- Thanks to [Claude](https://www.anthropic.com/claude) and [Claude Code](https://claude.ai/code) for assistance during development
- Thanks to [ArXiv](https://arxiv.org/), [OpenAlex](https://openalex.org/), [Semantic Scholar](https://www.semanticscholar.org/) for providing open academic data
- Thanks to [MinerU](https://mineru.net/) for providing cloud PDF parsing capabilities

---

## 📝 Changelog

See **[CHANGELOG.md](CHANGELOG.md)** for the complete version history.

### Recent Version Summary

<table>
<tr><th>Version</th><th>Date</th><th>Type</th><th>Highlights</th></tr>
<tr><td><b>v4.0</b></td><td>2026-08-22</td><td>🚀 Major release</td><td>SQLite daily history with exact-version delivery, durable pending-paper queue (max_papers_per_run), full arXiv pagination with terminal scan receipts, declarative extra data sources (core narrowed to arXiv + PRL), Hugging Face Papers source, read-only scoring diagnostics, Docker image split and security fixes (WebUI bound to 127.0.0.1 by default, no secrets leaked to cron), and a large reliability hardening pass (fail-closed state, atomic delivery, boundary hardening)</td></tr>
<tr><td><b>v3.2</b></td><td>2026-04-26</td><td>✨ Enhancement + 🐛 Fix</td><td>Network proxy (per-service granularity), WebDAV data sync (with Jianguoyun compatibility fix), one-click config export, Docker update notifications, Daily Push tab (reorganized run manager), independent Markdown/HTML report toggles, trend analysis dual-toggle output, run log auto-redirect, ArXiv fetch optimization with early stopping, configurable daily deep analysis</td></tr>
<tr><td><b>v3.1</b></td><td>2026-04-15</td><td>✨ Enhancement + 🐛 Fix</td><td>Run Manager tab, log viewer upgrade, Trend Analysis tab, report viewer enhancements, ArXiv timeout guard, stale lock reclamation</td></tr>
<tr><td><b>v3.0</b></td><td>2026-03-09</td><td>✨ Major</td><td>Trend research mode, trend analysis GitHub Actions workflow, comprehensive trend analysis, token tracking, auto-trigger setup wizard, concurrent run mutex locks, per-run log files, Streamlit config panel (with report viewer), keyword trend HTML reports</td></tr>
</table>

[View full changelog →](CHANGELOG.md)

---

<div align="center">

If this project helps you, please give it a **Star** ⭐

[![Star History Chart](https://api.star-history.com/svg?repos=yzr278892/arxiv-daily-researcher&type=Date)](https://star-history.com/#yzr278892/arxiv-daily-researcher&Date)

[![Issues](https://img.shields.io/github/issues/yzr278892/arxiv-daily-researcher?style=flat-square&label=Issues)](https://github.com/yzr278892/arxiv-daily-researcher/issues)
[![Email](https://img.shields.io/badge/Email-Contact-blue?style=flat-square)](mailto:yzr278892@gmail.com)

</div>
