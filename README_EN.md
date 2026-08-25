<div align="center">

# 🔬 ArXiv Daily Researcher

**LLM-powered academic paper monitoring, filtering, deep analysis & trend research**

[![Version](https://img.shields.io/badge/version-4.0-brightgreen.svg)](CHANGELOG.md)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?logo=docker&logoColor=white)](https://www.docker.com/)
[![GitHub Actions](https://img.shields.io/badge/GitHub_Actions-Supported-2088FF?logo=github-actions&logoColor=white)](https://github.com/features/actions)
[![Streamlit](https://img.shields.io/badge/Config_Panel-Streamlit-FF4B4B?logo=streamlit&logoColor=white)](#%EF%B8%8F-streamlit-config-panel)
[![中文](https://img.shields.io/badge/README-中文-blue.svg)](README.md)

*High-quality paper digests every day; a year of research trends in one command; one panel for config, runs, previews and troubleshooting.*

</div>

---

ArXiv Daily Researcher automatically fetches papers from **ArXiv** and **declaratively extensible journal sources** (PRL, PRA/PRB, Nature, Science, Hugging Face Papers, …), filters relevant work with a configurable keyword-weight scoring system, downloads PDFs for deep analysis, tracks keyword evolution, generates Markdown / HTML reports and pushes results to multiple notification channels. All paper identity, stage state and delivery history persist in **SQLite**: each exact version is delivered once, new versions are re-processed automatically, and interrupted runs resume from the queue.

Current version supports:
- **Daily research mode** — routine monitoring and high-relevance tracking (default 12:00 each day, configurable)
- **Trend research mode** — mid/long-term insight for a chosen topic
- **Streamlit panel** — 12 tabs covering config, runs, progress, favorites, search, previews and troubleshooting
- **Silent midnight keyword maintenance** — LLM batch normalization and trend reports run independently at 00:00

---

## ✨ Core Features

<table>
<tr>
<td colspan="2" align="center"><sub>— Fetching & Filtering —</sub></td>
</tr>
<tr>
<td width="50%" valign="top">

### 📡 Multi-Source Fetching

Core sources are **ArXiv** (official API, full pagination, submitted+updated dual queries) and PRL; other sources (PRA/PRB, Nature, Science, Hugging Face Papers, …) are **declarative JSON definitions disabled by default** — enable them from a dropdown in the panel. Journal papers with an ArXiv version automatically switch to ArXiv for richer abstracts and PDFs. Optional **Semantic Scholar** enrichment (citations, AI TLDR). A failed source scan fails the run explicitly; watermarks only advance after complete runs.

</td>
<td width="50%" valign="top">

### 🎯 Three Scoring Strategies

`CHEAP_LLM` scores each paper per keyword (0–10):

- **v1 weighted scoring** — keyword weight sum + expert-author bonus + dynamic pass line
- **Core relevance V2** — improved relevance-oriented scoring
- **Learned mode (`learned_preference_v1`)** — v1 plus a correction library learned continuously from likes/dislikes (strong signals) and passing history (weak signals); per-term clamping, global decay, learned influence always below directly configured keywords

</td>
</tr>
<tr>
<td colspan="2" align="center"><sub>— Deep Analysis & Knowledge —</sub></td>
</tr>
<tr>
<td width="50%" valign="top">

### 🔍 Deep PDF Analysis

Papers passing the filter get their PDF downloaded and analyzed by `SMART_LLM` across **methods, innovations, tech stack, key findings, limitations, relations and future directions**. **PyMuPDF local parsing is the default**, with optional **MinerU** cloud parsing and automatic fallback. Reports mark whether the TLDR came from full-text parsing.

</td>
<td width="50%" valign="top">

### 📈 Keyword Trend Tracking

Keywords extracted during scoring are stored in SQLite; **an independent cron job silently runs LLM batch normalization every day at 00:00** (synonym merging, abbreviation expansion, spelling unification) without touching the main pipeline; generates HTML trend reports (colored bar charts, heatmaps) at a configurable frequency.

</td>
</tr>
<tr>
<td colspan="2" align="center"><sub>— Trend Research & Cost Observability —</sub></td>
</tr>
<tr>
<td width="50%" valign="top">

### 🔬 Trend Research Mode

The standalone `trend_research` mode takes keywords, a date range and **full ArXiv category dropdown filtering** (153 primary categories, alphabetical), batch-retrieves papers, generates per-paper TLDRs and one comprehensive `SMART_LLM` analysis. Custom analysis prompt templates are supported.

</td>
<td width="50%" valign="top">

### 📊 Token Tracking

A thread-safe counter tracks per-model input/output usage, persisted to SQLite for every run (success/failure/interrupt). The Analytics tab shows today/30-day totals, a one-month heatmap, static adaptive line charts and per-model breakdowns — retained forever.

</td>
</tr>
<tr>
<td colspan="2" align="center"><sub>— Reports & Feedback —</sub></td>
</tr>
<tr>
<td width="50%" valign="top">

### 📄 Dual-Format Reports & Favorites

Three report families (daily / trend / keyword-trend), each with independently switchable Markdown (archival) and HTML (browser reading, KaTeX). Mark papers 👍/👎 directly inside report cards (no flash, instant persistence); the Favorites & Search page lists liked papers chronologically (titles link to arXiv) with keyword and top-author statistics.

</td>
<td width="50%" valign="top">

### 🔎 Full-History Paper Search

Metadata search over SQLite: title/author/abstract/TLDR/extracted-keyword matching plus source, processing-date range, minimum score and favorites-only filters with pagination. Data is archived permanently — nothing gets lost as history grows.

</td>
</tr>
<tr>
<td colspan="2" align="center"><sub>— Legacy Migration & Backfill —</sub></td>
</tr>
<tr>
<td width="50%" valign="top">

### 📜 Legacy History Import + Range Scan

Upgrading from v3.x: one click on **Read Legacy History** (Data Management) parses the v3.2 history JSON and every HTML report card into SQLite (scores, translations, deep analyses included) — newest analysis wins on duplicates, incomplete records go to a retry backlog, and the import is idempotent. A chunked **range re-scan of arXiv** then finds papers the old deployment missed. Both jobs queue up at idle time and never collide with a running daily research.

</td>
<td width="50%" valign="top">

### 🧩 Supplement Reports + Past-Date Dailies

After Read Legacy History finishes, its backlog (missing data + missed papers) automatically reruns through the daily pipeline as one **supplement report** — same format, capped by the per-run limit. The Daily Push tab can also queue a **past-date range** and rebuild each day in date order; each filename timestamp is that past date plus the actual run time, so reports line up with genuine history under Reports → Daily Research.

</td>
</tr>
<tr>
<td colspan="2" align="center"><sub>— Config & Operations —</sub></td>
</tr>
<tr>
<td width="50%" valign="top">

### 🧙 Wizard + Panel

A 7-step CLI wizard bootstraps first deployments (auto-triggered on first Docker start); the **Streamlit panel** (12 tabs) handles daily tuning, run-now, live progress and logs, and report previews. Pages you never visited keep their on-disk values on save.

</td>
<td width="50%" valign="top">

### 🛡️ Production-Grade Reliability

**SQLite persistent queue** (resume after interruption, failed papers retried first, per-run cap against first-deploy floods), **atomic delivery** (report + delivery + notifications + maintenance in one transaction), **shared LLM timeout/retry policy** (per-request timeout, exponential backoff with jitter, Retry-After honored, fast-fail on auth errors), **arXiv rate-limit backoff with cross-domain cooldown**, **no-progress watchdog**, **file locks**, **gzip DB backups** (all local copies made today are retained; each earlier day keeps only its newest copy before configurable age cleanup, 7 days by default; enter `0` to disable age expiry; incremental WebDAV mirror never deletes remote files), **per-service proxy**.

</td>
</tr>
</table>

---

## 📑 Navigation

<table>
<tr>
<td width="50%" valign="top">

### 📘 Getting Started

|          Section           | Summary                    |
| :------------------------: | :------------------------- |
| [✨ Core Features](#-core-features) | Capability overview |
| [🚀 Quick Start](#-quick-start)     | Three steps to first run  |
| [🛠️ Config Tools](#%EF%B8%8F-config-tools) | CLI wizard + panel |
| [🐳 Deployment](#-deployment)       | Docker / Actions / cron    |

</td>
<td width="50%" valign="top">

### 📗 In Depth

|            Section            | Summary                          |
| :--------------------------: | :------------------------------- |
|  [📖 Details](#-feature-details)   | Modes, reports, notifications, locks |
|  [📁 Structure](#-project-structure) | Directory & module map          |
|  [❓ FAQ](#-faq)                    | 11 practical troubleshooting guides |
| [📝 Changelog](CHANGELOG.md)       | Full version history              |

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
pip install -r requirements-core.txt              # panel additionally needs requirements-webui.txt
```

### Step 2: Configure

Start with the interactive wizard:

```bash
python src/utils/setup_wizard.py
```

It walks you through LLM config, search parameters, sources, keywords & research context, scoring, notifications and advanced settings, then generates `.env` and `configs/config.json` (JSONC — handwritten comments are preserved on panel saves).

> [!TIP]
> With existing config the wizard pre-fills current values; change what you want and press Enter to keep the rest.

<details>
<summary><b>Manual configuration (skip the wizard)</b></summary>

**1) Copy the template:**

```bash
cp .env.example .env
```

**2) Fill in LLMs:**

```env
CHEAP_LLM__API_KEY=sk-your-key
CHEAP_LLM__BASE_URL=https://api.openai.com/v1
CHEAP_LLM__MODEL_NAME=gpt-4o-mini

SMART_LLM__API_KEY=sk-your-key
SMART_LLM__BASE_URL=https://api.openai.com/v1
SMART_LLM__MODEL_NAME=gpt-4o
```

**3) Core keywords and domains:**

```jsonc
{
  "keywords": {
    "primary_keywords": {
      "weight": 1.0,
      "keywords": ["quantum error correction", "surface code"]
    },
    "research_context": "My research focuses on fault-tolerant quantum computation"
  },
  "target_domains": {
    "domains": ["quant-ph"]
  }
}
```

</details>

### Step 3: Run

```bash
# Daily research (default)
python main.py

# Trend research
python main.py --mode trend_research --keywords "quantum error correction"
```

Outputs go to `data/reports/` and `logs/` by default.

---

## 🛠️ Config Tools

Two main configuration paths: the **CLI wizard** and the **Streamlit panel**.

### 🧙 Interactive Setup Wizard

For first deployments, SSH and headless servers:

```bash
python src/utils/setup_wizard.py
```

| Step | Content       | Notes                                            |
| :--: | :------------ | :----------------------------------------------- |
|  1   | LLM config    | Provider, API key, optional connectivity test    |
|  2   | Search        | Days, max results per source                     |
|  3   | Sources       | ArXiv & journals, ArXiv categories               |
|  4   | Keywords      | Primary keywords, reference PDFs, context        |
|  5   | Scoring       | Base score, weight coefficient, author bonus     |
|  6   | Notifications | Channels and credentials                         |
|  7   | Advanced      | PDF parsing, concurrency, log retention          |

Existing config is backed up to `.bak` before writing.

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

Visit `http://localhost:8501` (Docker binds `127.0.0.1` only).

The panel shares `.env` and `configs/config.json` with the worker; changes take effect on the next run. The sidebar offers save, reload-from-disk and **🔄 restart worker container** (requests a worker restart via the shared volume; cron is reinstalled from the latest config).

#### 12 Tabs Overview

|  #    | Tab                    | What it does                                                                                                                                                  |
| :---: | :--------------------- | :------------------------------------------------------------------------------------------------------------------------------------------------------------ |
|   1   | **Daily Push**         | Run-now button; a peer-level **Past Daily Reports** section below it (pick a date range, persist it in a queue, rebuild each day in order, then click Start Run); then **live run status** (lock/PID + **phase heartbeat progress**: prepare → scan → score/translate → analyze → report, with registered/scored/analyzed/failed counts and elapsed time, 5s auto-refresh); stop (with confirmation); **daily research settings** (daily run time / HTML / Markdown / include-all / per-run cap); log viewer |
|   2   | **Reports**            | Daily / trend / keyword-trend HTML reports with preview and date navigation; mark papers 👍/👎 inside report cards (flash-free, persisted instantly)           |
|   3   | **Favorites & Search** | **Liked papers** (chronological, titles hyperlink to arXiv, 👍/👎 metrics) + **keyword statistics** (liked-paper keyword frequency, top authors) + **paper search** (full history) |
|   4   | **Trend Analysis**     | Keywords, date range, **full ArXiv category dropdown**, sorting, max results, TLDR, output formats and skills; custom analysis prompts (save/apply/delete templates); start/stop |
|   5   | **Keywords**           | Research context (top), primary keywords, reference-PDF extraction (high/medium/low weight tiers + extracted list), similarity threshold                        |
|   6   | **Data Sources**       | Source toggles, extra sources (dropdown multi-select + custom), **full ArXiv category multi-select**, fetch timeout         |
|   7   | **Scoring**            | Strategy (v1 / V2 / learned), pass-line formula, per-keyword cap, author bonus, learned-library preview, live scoring preview                                  |
|   8   | **Analytics**          | Token usage (today/30-day totals, one-month heatmap, adaptive line chart, per-model), source health (last 20 scan receipts), compact run diagnostics           |
|   9   | **Notifications**      | Global toggle, success/failure/attachment control, six channels, SMTP test                                                                                    |
|  10   | **Data Management**    | Config export (zip), **WebDAV sync** (manual / scheduled / after-report), **DB backup** (gzip; configurable local full-backup retention, 7 days by default; enter `0` to keep forever; incremental WebDAV mirror, run now) + **legacy history import** (read legacy history + range scan + supplement report, idle-time)                               |
|  11   | **API**                | CHEAP_LLM / SMART_LLM / MinerU with connectivity tests                                                                                                        |
|  12   | **Advanced**           | PDF parser (pymupdf default), concurrency, token tracking, update checks, keyword tracking, retries, log rotation, stale-lock recovery, **proxy**              |

### 🖼️ WebUI Screenshots

<table>
  <tr>
    <td align="center" width="50%">
      <img src="assets/img_en.png" alt="English WebUI" width="100%" />
      <br />
      <sub>English WebUI main screen</sub>
    </td>
    <td align="center" width="50%">
      <img src="assets/img_noti.png" alt="Notification settings" width="100%" />
      <br />
      <sub>Notification settings (Chinese)</sub>
    </td>
  </tr>
  <tr>
    <td align="center" width="50%">
      <img src="assets/img_prev.png" alt="Report preview" width="100%" />
      <br />
      <sub>Report preview (Chinese)</sub>
    </td>
    <td align="center" width="50%">
      <img src="assets/img_serh.png" alt="Search sources settings" width="100%" />
      <br />
      <sub>Search & sources settings (Chinese)</sub>
    </td>
  </tr>
</table>

<details>
<summary><b>Wizard vs panel — which one?</b></summary>

| Tool                              | Best for                       | Traits                                            |
| :-------------------------------- | :----------------------------- | :------------------------------------------------ |
| **Wizard** (`setup_wizard.py`)    | First deploy, SSH, headless    | CLI, init-friendly, connectivity test             |
| **Panel** (`config_panel.py`)     | Daily tuning, previews, debug  | 12 tabs, WYSIWYG, run management & trend analysis |

**Recommendation**: wizard first, panel for daily use.

</details>

---

## 🐳 Deployment

### Docker <sup>Recommended</sup>

Docker is the recommended long-running deployment. Compose defines two containers (one image, two build targets):
- **arxiv-daily-researcher** (worker): scheduled jobs, trigger watcher, report generation; `network_mode: host` for direct access to host-local LLMs
- **arxiv-daily-researcher-config-panel** (WebUI): Streamlit panel bound to `127.0.0.1:8501`, cooperating with the worker through shared volumes

#### Start

```bash
git clone https://github.com/yzr278892/arxiv-daily-researcher.git
cd arxiv-daily-researcher
cp .env.example .env
docker compose up -d
```

Three scheduled jobs run inside the worker (timezone `TZ`, default `Asia/Shanghai`):

| Time         | Job                              | Notes                                                                                     |
| :----------- | :------------------------------- | :---------------------------------------------------------------------------------------- |
| Configurable | Daily research                   | `daily_research.run_time` (HH:MM, **default 12:00**) in `configs/config.json`; adjust in the Daily Push tab; effective after container restart |
| `0 0 * * *`  | Keyword normalization + reports  | Silent; logs to `logs/keyword_*.log`; failures never affect the daily report              |
| Every minute | WebDAV scheduled-sync tick       | Transfers only when scheduled mode is selected and the expression matches                 |

Other defaults:
- `RUN_ON_STARTUP=false` — the worker does not run immediately on start (set `true` to change)
- `MODE=cron` — scheduled mode (`run-once` executes once and exits)
- `SETUP_WIZARD=auto` — the wizard auto-triggers on first deployment (no `.env`)

> [!NOTE]
> The daily run time is **no longer controlled by an environment variable**: change `daily_research.run_time`, then click "🔄 restart worker container" in the sidebar (or `docker compose restart arxiv-daily-researcher`) to reinstall cron.

#### Common commands

```bash
docker compose ps
docker compose logs -f
docker compose up -d config-panel && docker compose stop config-panel

# Trend research inside the container
docker exec -it arxiv-daily-researcher python main.py --mode trend_research \
  --keywords "quantum error correction" \
  --date-from 2025-01-01 \
  --categories quant-ph

# Manual keyword maintenance (normally via the 00:00 cron)
docker exec arxiv-daily-researcher python -m modes.keyword_maintenance

docker compose down
```

#### WebUI run-now mechanism

The panel asks the worker to run jobs through an **atomic JSON request queue** on a shared volume (no Docker socket needed):
1. Clicking run-now atomically writes `data/run/webui_triggers/<ts>_<id>.json`
2. The worker's `trigger_watcher` polls every 5s and atomically claims requests via `mv` (no double execution)
3. The claimed request launches `python main.py --mode daily_research`; the real PID lands in `data/run/webui_triggered.pid`
4. Logs go to `logs/manual_*.log`; terminal states (with a safe error summary) to `webui_triggers/status/`
5. If the same task is already running, the trigger is recorded as `skipped_busy` (exit code 75) instead of fake success

The restart button delivers a `restart_worker.request` marker the same way; the worker archives it and sends TERM to PID 1.

<details>
<summary><b>Container environment variables</b></summary>

| Variable          | Default          | Notes                                        |
| :---------------- | :--------------- | :------------------------------------------- |
| `TZ`              | `Asia/Shanghai`  | Timezone (affects all cron times)            |
| `RUN_ON_STARTUP`  | `false`          | Run once immediately on start                |
| `MODE`            | `cron`           | `cron` or `run-once`                         |
| `SETUP_WIZARD`    | `auto`           | `auto` / `true` / `false`                    |

> The daily run time comes from `configs/config.json` (`daily_research.run_time`); there is no environment override.

</details>

<details>
<summary><b>Local LLMs (Ollama etc.)</b></summary>

The worker uses `network_mode: host`, so host-local services are directly reachable:

```env
CHEAP_LLM__API_KEY=ollama
CHEAP_LLM__BASE_URL=http://127.0.0.1:11434/v1
CHEAP_LLM__MODEL_NAME=qwen2.5:7b
```

</details>

---

### GitHub Actions

For machines without an always-on server. Two workflows: `daily-run.yml` and `trend-research.yml`.

> [!IMPORTANT]
> GitHub Actions suits light or trial usage — please respect Actions quotas. The `schedule:` trigger in `daily-run.yml` is commented out by default. **For production, prefer Docker.**

#### Setup

1. Fork the repo
2. **Settings → Secrets and variables → Actions**
3. Provide at least:

| Secret                  | Required | Notes                          |
| :---------------------- | :------: | :----------------------------- |
| `CHEAP_LLM_API_KEY`     |    ✅    | Cheap LLM API key              |
| `CHEAP_LLM_BASE_URL`    |    ✅    | Cheap LLM base URL             |
| `CHEAP_LLM_MODEL_NAME`  |    ✅    | Cheap LLM model                |
| `SMART_LLM_API_KEY`     |    ✅    | Smart LLM API key              |
| `SMART_LLM_BASE_URL`    |    ✅    | Smart LLM base URL             |
| `SMART_LLM_MODEL_NAME`  |    ✅    | Smart LLM model                |
| Notification secrets    | optional | SMTP / Telegram / webhook etc. |

`trend-research.yml` accepts `keywords`, `date_from`, `date_to`, `categories`, `sort_order`, `max_results`; reports are kept as artifacts for 30 days.

---

### Local cron

Without Docker or Actions, use system cron:

```bash
crontab -e
# Daily research (12:00 example) + silent keyword maintenance at 00:00
0 12 * * * cd /path/to/arxiv-daily-researcher && ./scripts/run_daily.sh >> /tmp/arxiv-cron.log 2>&1
0 0 * * * cd /path/to/arxiv-daily-researcher && PYTHONPATH=src python -m modes.keyword_maintenance >> /tmp/arxiv-keyword.log 2>&1
```

---

## 📖 Feature Details

### 🔄 Two run modes

| Dimension   | `daily_research` (default)     | `trend_research`               |
| :---------- | :----------------------------- | :----------------------------- |
| Purpose     | Daily tracking of new papers   | Long-range topic analysis      |
| Sources     | ArXiv + declarative journals   | ArXiv                          |
| Time range  | Fixed last 3 days (+ announcement grace; past dates backfillable) | Arbitrary range |
| Filtering   | Weighted scoring (3 strategies) | None — keep everything        |
| Analysis    | PDF deep analysis for top papers | Per-paper TLDR + synthesis    |
| Triggered   | Cron / Docker / Actions / panel | CLI / panel / Actions         |
| Output      | `data/reports/daily_research/` | `data/reports/trend_research/`|

### 📅 Daily pipeline

```text
1. Prepare keywords and the dynamic pass line
2. Fetch from ArXiv / journals (candidates atomically registered into the SQLite queue)
3. Score + translate per queue order (exact delivered versions skipped; new versions re-processed)
4. Deep-analyze papers that passed
5. Generate Markdown / HTML reports and commit delivery atomically
6. Send notifications, run DB backup and WebDAV maintenance
```

Every phase transition writes a heartbeat; the Daily Push tab shows the current phase (prepare/scan/score/analyze/report) plus registered/scored/analyzed/failed counts. Daily reports cover a **fixed 3-day window** and process every new paper in it (failed runs widen the watermark window automatically; delivered versions are deduplicated by the ledger) — older dates are rebuilt through past-date reports. `max_papers_per_run` (default 200, `0` = unlimited) prevents first-deploy floods; leftovers stay queued and failed papers are retried first; supplement reports obey the same cap.

### 🧩 Backfill & Legacy Migration

- **Legacy history import + automatic supplement** (`--mode legacy_import`, one click in Data Management): parses the v3.2 history JSON and all HTML reports into SQLite, then re-scans the covered date range on arXiv for missed papers. Missing and missed papers automatically continue into one capped supplement report within that same workflow; delivered entries settle out, and failures retry on the next Read Legacy History.
- **Past-date daily report** (`--mode backfill_run --date-from YYYY-MM-DD --date-to YYYY-MM-DD`): persists every selected date in a durable queue and runs them in date order, each with full scoring/translation/analysis. A failed day remains recorded without blocking later days; the report filename timestamp is the past date plus actual run time.
- These workflows are idle-time jobs: they wait for daily, trend, and maintenance work, do not collide, and are resumable/idempotent.

### 🎯 Dynamic pass line

```text
pass line = base_score + weight_coefficient × Σ(keyword weights)
```

Defaults are `base_score = 1.5`, `weight_coefficient = 2.5` — adjustable in the Daily Push tab or `configs/config.json`.

### 🛡️ LLM & arXiv retry policy

All OpenAI clients share one set of bounds (the `llm` section of `configs/config.json`):

| Setting                      | Default | Meaning                                                     |
| :--------------------------- | :-----: | :---------------------------------------------------------- |
| `llm.timeout_seconds`        |   300   | Per-request HTTP timeout                                    |
| `llm.sdk_max_retries`        |    1    | SDK-level quick retries (connection blips / Retry-After)    |
| `llm.retry_max_attempts`     |    5    | Application-level attempt cap                               |
| `llm.retry_min_wait`         |    5    | Backoff start (seconds, jittered)                           |
| `llm.retry_max_wait`         |   120   | Backoff cap (seconds)                                       |

429/5xx/timeouts/empty bodies retry with exponential backoff; 401/403/404/400 fail fast. A global request pool (`llm_request_pool.requests_per_minute`, default 30) paces low-concurrency relays.

On the arXiv side: a no-progress watchdog (default 180s, legitimately extended while results keep arriving), exponential backoff for rate limits (60→480s), linear backoff otherwise, **Retry-After honored**, and a 60s cooldown between domains after a failed one. Domain scans and keyword search share the same policy.

### 📡 Sources & ArXiv-first policy

- ArXiv: official `arxiv` library, 6s inter-page delay, submitted+updated dual queries, full pagination
- Journals: via OpenAlex; declarative definitions enable/customize in the panel
- Journal papers with an ArXiv version switch to ArXiv metadata and PDFs
- Optional Semantic Scholar enrichment

### 🔍 PDF parsing & fallback

| Mode      | Strengths                          | Limits                  |
| :-------- | :--------------------------------- | :---------------------- |
| `pymupdf` | Local, zero external deps (**default**) | Quality depends on PDF |
| `mineru`  | Better structure for complex papers | Requires a token        |

MinerU outages fall back to PyMuPDF automatically.

### 🔒 Concurrency locks

| Mode             | Lock file                               |
| :--------------- | :-------------------------------------- |
| `daily_research` | `data/run/daily_research.lock`          |
| `trend_research` | `data/run/trend_research_<hash8>.lock`  |

Duplicate starts exit safely; locks carry PID and start time; stale locks are reclaimed after 12h by default; reclaim failures exit conservatively.

### 📄 Reports

| Report          | Path                                                                 |
| :-------------- | :------------------------------------------------------------------- |
| Daily           | `data/reports/daily_research/{markdown,html}/<source>/`              |
| Trend           | `data/reports/trend_research/{markdown,html}/<slug>/` (+ metadata)   |
| Keyword trend   | `data/reports/keyword_trend/{markdown,html}/`                        |

Markdown / HTML are independently switchable. Daily reports include a summary, passing-paper details (with deep analysis and full-text TLDR provenance), the not-passed list, keyword charts and token usage.

### 🔔 Notifications

Email, WeCom, DingTalk, Telegram, Slack and generic webhooks. Two switch layers (global + per channel); a channel fires only when fully configured and enabled. Delivery goes through a SQLite outbox — failures are retained and retried, never silently dropped.

---

## 📁 Project Structure

```text
arxiv-daily-researcher/
├── main.py                          # CLI entry, mode dispatch
├── .env.example                     # Environment template
├── requirements-core.txt            # worker deps (requirements-webui.txt for the panel)
├── README.md / README_EN.md
│
├── src/
│   ├── config.py                    # Config loading (.env + JSONC config.json)
│   ├── scoring_policy.py            # v1 / core_v2 / learned_preference_v1
│   ├── modes/
│   │   ├── daily_research.py        # Daily pipeline
│   │   ├── trend_research.py        # Trend pipeline
│   │   └── keyword_maintenance.py   # Silent midnight keyword job
│   ├── agents/                      # LLM agents
│   ├── sources/                     # ArXiv / OpenAlex / HF Papers / orchestration
│   ├── report/                      # daily / trend / keyword_trend rendering
│   ├── notifications/               # Multi-channel notifications
│   ├── parsers/                     # PDF parsing (PyMuPDF / MinerU)
│   ├── keyword_tracker/             # Keyword tracking & normalization
│   ├── utils/
│   │   ├── config_io.py             # JSONC I/O (comments preserved)
│   │   ├── daily_research_store.py  # SQLite store (queue/delivery/preferences/usage)
│   │   ├── llm_resilience.py        # Shared LLM timeout & retry policy
│   │   ├── llm_request_pool.py      # Global LLM rate limiting
│   │   ├── run_lock.py / webui_trigger.py / backup.py / webdav_sync.py …
│   └── webui/                       # Streamlit panel
│       ├── config_panel.py
│       ├── i18n.py                  # zh/en bilingual
│       ├── arxiv_categories.py      # 153 ArXiv primary categories
│       ├── report_component/        # Report preview component (flash-free marking)
│       └── tabs/                    # 12 tab modules
│
├── configs/
│   ├── config.json                  # Main config (JSONC)
│   └── templates/                   # Report / notification / email templates
│
├── docker-compose.yml               # Two-container compose (worker + panel)
├── docker/
│   ├── Dockerfile                   # Multi-stage: worker / webui targets
│   └── entrypoint.sh                # cron install / trigger watcher / restart
│
├── VERSION                          # Version (update checks)
├── scripts/                         # Run scripts & Makefile
├── assets/                          # README / WebUI screenshots
├── data/                            # Runtime data (SQLite, reports, trigger queue)
└── logs/                            # System & per-run logs
```

---

## ❓ FAQ

<details>
<summary><b>1. WebDAV to Jianguoyun keeps failing with 403?</b></summary>

Jianguoyun's WebDAV does not support HTTP HEAD, which most clients use for existence checks. This project already uses PROPFIND instead. Also verify:
- The URL ends with `https://dav.jianguoyun.com/dav/`
- The password is an **app-specific password** (generated in account security settings), not the login password
- "Test connection" in the Data Management tab passes
</details>

<details>
<summary><b>2. How to pick trend-research parameters?</b></summary>

- **Date range**: 90–180 days for first use
- **Categories**: constrain with the full-category dropdown (e.g. `quant-ph · Quantum Physics`)
- **Output**: Markdown and HTML toggle independently in the Trend Analysis tab
- **Skill**: default `comprehensive_analysis` covers all five dimensions in one pass
- **max_results**: default 500; lower to 200 if analysis is slow, raise to 1000 for broad topics
</details>

<details>
<summary><b>3. "Already running" but I suspect a stale lock?</b></summary>

Multiple safeguards exist:
- Dead-process locks are reclaimed automatically (PID liveness check at startup)
- Locks older than `run_lock_max_age_hours` (default 12h) are reclaimed
- The panel's stop button (with confirmation) keeps completed stages and re-queues unfinished papers

> [!WARNING]
> Only remove lock files manually when the PID is definitely dead; deleting a live lock can cause duplicate runs.
</details>

<details>
<summary><b>4. Local LLMs (Ollama / vLLM / LocalAI) in Docker?</b></summary>

The worker uses `network_mode: host`:

```env
CHEAP_LLM__API_KEY=ollama
CHEAP_LLM__BASE_URL=http://127.0.0.1:11434/v1
CHEAP_LLM__MODEL_NAME=qwen2.5:7b
```

For bridge networks (the panel container), replace `127.0.0.1` with `host.docker.internal` (Windows/Mac) or the host IP (Linux). Make sure the LLM listens on `0.0.0.0`.
</details>

<details>
<summary><b>5. How do run-now and container-restart cooperate with the worker?</b></summary>

Everything rides on **shared-volume messaging**, no Docker socket:

- **Run now**: the panel atomically writes `data/run/webui_triggers/<ts>_<id>.json` → the worker's `trigger_watcher` polls every 5s and claims it via `mv` → launches `python main.py --mode daily_research` (PID in `webui_triggered.pid`, logs in `logs/manual_*.log`)
- **Restart container**: the sidebar button writes `restart_worker.request` → the worker archives the marker and TERMs PID 1 → the container restarts and reinstalls cron from the latest config
- **Stop run**: the panel forwards SIGTERM to the real PID via the shared volume; unfinished papers stay queued

Both containers must mount the **same** `data/` and `logs/` volumes.
</details>

<details>
<summary><b>6. Proxy configuration — per service?</b></summary>

In Advanced → proxy or the `proxy` block of `configs/config.json`:

- **Global switch**: `proxy.enabled`
- **Address**: `proxy.url`, HTTP/SOCKS5 (e.g. `http://127.0.0.1:7890`)
- **Per-service scope** (`proxy.scope`): ArXiv, OpenAlex, Semantic Scholar, LLM API, notifications, update checks

Docker notes: `127.0.0.1` under `network_mode: host`; bridge networks on Linux need `--add-host=host.docker.internal:host-gateway`.
</details>

<details>
<summary><b>7. Docker <code>NameResolutionError</code> / cannot resolve <code>export.arxiv.org</code>?</b></summary>

This is normally a **NAS/host network, Docker DNS, or Tailscale DNS refresh problem after a network change**, not an arXiv-fetching or application-code defect. A typical message is:

```text
HTTPSConnectionPool(host='export.arxiv.org', ...)
... NameResolutionError: Temporary failure in name resolution
```

Check name resolution on both the host and the worker first:

```bash
getent hosts export.arxiv.org
docker exec arxiv-daily-researcher getent hosts export.arxiv.org
docker exec arxiv-daily-researcher cat /etc/resolv.conf
```

After moving a NAS, changing DHCP/router settings, switching networks, or toggling Tailscale, repair the NAS's upstream DNS first, then recreate the application containers so Docker regenerates their `resolv.conf`:

```bash
docker compose up -d --force-recreate
```

If DHCP-provided DNS is repeatedly unreliable, add a **local** `docker-compose.override.yml` (rather than baking one location's DNS into the project's default compose file), then recreate the containers:

```yaml
services:
  arxiv-daily-researcher:
    dns:
      # Keep only when Tailscale MagicDNS is enabled.
      - 100.100.100.100
      # A China-mainland public fallback; use reliable local DNS elsewhere.
      - 223.5.5.5
  config-panel:
    dns:
      - 100.100.100.100
      - 223.5.5.5
```

`100.100.100.100` is Tailscale's MagicDNS address, not a general public resolver. Remove it when MagicDNS is not in use and configure two resolvers appropriate for the local network. The project retries network requests, but a container with no working DNS must be repaired at the deployment layer.
</details>

<details>
<summary><b>8. Markdown vs HTML — can I generate only one?</b></summary>

Same content, different formats: Markdown for Git/archival, HTML for reading and sharing with KaTeX. Toggle each independently in Daily Push (daily reports) or Trend Analysis (trend reports).
</details>

<details>
<summary><b>9. When does keyword normalization run?</b></summary>

1. `CHEAP_LLM` extracts keywords during scoring into SQLite
2. **Every day at 00:00** a standalone cron job (`modes/keyword_maintenance`) silently runs LLM batch normalization; failures only log and retry the next night — the daily report is never affected
3. Trend reports are generated at the configured frequency (daily/weekly/monthly/always)

Disable tracking via `keyword_tracker.enabled` (or just normalization via `keyword_tracker.normalization.enabled`).
</details>

<details>
<summary><b>10. MinerU vs PyMuPDF? Expired MinerU token?</b></summary>

| Scenario                            | Mode                 |
| :---------------------------------- | :------------------- |
| No external deps, offline, stability | `pymupdf` (default) |
| Best structure for complex layouts  | `mineru`             |

Switch in the API or Advanced tab. MinerU tokens last 3 months; on expiry the system **falls back to PyMuPDF** without interrupting the run and sends an alert; renew at [mineru.net](https://mineru.net/apiManage/apiKey).
</details>

<details>
<summary><b>11. Sharing config and reports across devices with WebDAV?</b></summary>

Three modes (Data Management tab):

| Mode       | Behavior                                    |
| :--------- | :------------------------------------------ |
| **Manual** | Upload/download buttons in the panel        |
| **Scheduled** | Cron expression (e.g. daily 23:00)       |
| **After report** | Auto-upload after each daily report   |

Scopes: config, history, keyword data, reports (config only by default). Typical setup: primary device "after report", secondary devices "manual".
</details>

---

## 📜 License

[AGPL-3.0](https://www.gnu.org/licenses/agpl-3.0.html)

| Term        | Meaning                                                    |
| :---------- | :--------------------------------------------------------- |
| ✅ Use      | Free to use, modify, distribute                             |
| ✅ Commercial | Commercial use allowed                                    |
| 📋 Source   | Modified versions must be open-sourced under the same license |
| 🌐 Network  | Network service use also requires source disclosure        |
| 📝 Notice   | Preserve original copyright and license                    |

---

## 💬 Community

- **🐛 Issues**: [GitHub Issues](https://github.com/yzr278892/arxiv-daily-researcher/issues)
- **🔀 PRs**: Fork → change → pull request
- **⭐ Star**: if this project helps you, a star means a lot

---

## 🤝 API Usage

| API                  | Compliance                                                                |
| :------------------- | :------------------------------------------------------------------------ |
| **ArXiv**            | Official `arxiv` library, built-in 6s delay, rate-limit backoff, Retry-After honored |
| **OpenAlex**         | Contact header; configure `OPENALEX_EMAIL` for the polite pool            |
| **Semantic Scholar** | User-Agent header; optional API key for higher rates                      |
| **MinerU**           | Respects the 2000-page daily priority quota                               |

> [!NOTE]
> All external API and LLM calls retry with jittered exponential backoff plus a global rate limiter; throttling and network hiccups do not interrupt runs, and auth-class errors fail fast with clear logs.

---

## 🙏 Acknowledgements

- [Claude](https://www.anthropic.com/claude) & [Claude Code](https://claude.ai/code) for development assistance
- [ArXiv](https://arxiv.org/), [OpenAlex](https://openalex.org/), [Semantic Scholar](https://www.semanticscholar.org/) for open scholarly data
- [MinerU](https://mineru.net/) for cloud PDF parsing

---

## 📝 Changelog

See **[CHANGELOG.md](CHANGELOG.md)** for the full history.

### Latest releases

<table>
<tr><th>Version</th><th>Date</th><th>Type</th><th>Highlights</th></tr>
<tr><td><b>v4.0</b></td><td>2026-08-23</td><td>🚀 Major</td><td>SQLite daily history with exact-version delivery, persistent processing queue, full arXiv pagination with scan receipts, declarative extra sources, learned-mode scoring, gzip DB backups (local full copies automatically cleaned by a configurable retention window, 7 days by default; enter `0` to keep forever; incremental never-deleting WebDAV mirror), full-history paper search, standalone Favorites & Search tab (chronological favorites with arXiv links + keyword stats), full ArXiv category dropdowns, panel-configurable daily run time (default 12:00, config-only), silent midnight keyword normalization, live phase progress for long runs, shared LLM timeout/retry hardening and unified arXiv backoff (Retry-After + cross-domain cooldown), one-click worker restart, live run monitoring and stop control, one-click v3.2 legacy history import with range re-scan and supplement reports, past-date daily report rebuilds (reports sorted by filename timestamp), fixed 3-day daily window (search-days config removed), Docker image split with security fixes, large-scale reliability hardening (fail-closed, atomic delivery, boundary hardening)</td></tr>
<tr><td><b>v3.2</b></td><td>2026-04-26</td><td>✨ Enhancement + 🐛 Fixes</td><td>Per-service proxy, WebDAV sync (with Jianguoyun compatibility), config export, Docker update notices, independent Markdown/HTML toggles, dual trend-output switches, ArXiv fetch tuning, configurable daily deep analysis</td></tr>
<tr><td><b>v3.1</b></td><td>2026-04-15</td><td>✨ Enhancement + 🐛 Fixes</td><td>Run manager tab, log viewer upgrades, trend analysis tab, report viewer improvements, ArXiv timeout guard, stale-lock recovery</td></tr>
<tr><td><b>v3.0</b></td><td>2026-03-09</td><td>✨ Major</td><td>Trend research mode, trend Actions workflow, comprehensive trend analysis, token tracking, auto-triggered wizard, concurrency locks, per-run logs, Streamlit panel with report viewer, keyword-trend HTML reports</td></tr>
</table>

[Full history →](CHANGELOG.md)

---

<div align="center">

If this project helps you, consider a **Star** ⭐

[![Star History Chart](https://api.star-history.com/svg?repos=yzr278892/arxiv-daily-researcher&type=Date)](https://star-history.com/#/yzr278892/arxiv-daily-researcher&Date)

[![Issues](https://img.shields.io/github/issues/yzr278892/arxiv-daily-researcher?style=flat-square&label=Issues)](https://github.com/yzr278892/arxiv-daily-researcher/issues)
[![Email](https://img.shields.io/badge/Email-Email%20Author-blue?style=flat-square)](mailto:yzr278892@gmail.com)

</div>
