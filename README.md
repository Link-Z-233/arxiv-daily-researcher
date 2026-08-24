<div align="center">

# 🔬 ArXiv Daily Researcher

**基于 LLM 的智能学术论文监控、筛选、深度分析与趋势研究系统**

[![Version](https://img.shields.io/badge/version-4.0-brightgreen.svg)](CHANGELOG.md)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?logo=docker&logoColor=white)](https://www.docker.com/)
[![GitHub Actions](https://img.shields.io/badge/GitHub_Actions-Supported-2088FF?logo=github-actions&logoColor=white)](https://github.com/features/actions)
[![Streamlit](https://img.shields.io/badge/Config_Panel-Streamlit-FF4B4B?logo=streamlit&logoColor=white)](#️-streamlit-配置面板)
[![English](https://img.shields.io/badge/README-English-blue.svg)](README_EN.md)

*每天接收高质量论文摘要；一行命令纵览一年研究趋势；一个面板完成配置、运行、预览与排障。*

</div>

---

ArXiv Daily Researcher 会自动从 **ArXiv** 与**可声明扩展的期刊源**（PRL、PRA/PRB、Nature、Science、Hugging Face Papers 等）抓取论文，利用可配置的关键词权重评分系统筛选相关工作，下载 PDF 进行深度分析，跟踪关键词演变趋势，生成 Markdown / HTML 报告，并将结果推送到多种通知渠道。所有论文身份、阶段状态与交付历史持久化在 **SQLite** 中：同一版本只交付一次、新版本自动重新推送、中断后可从队列续跑。

当前版本支持：
- **每日研究模式**：面向日常监控与高相关论文追踪（默认每天 12:00，可配置）
- **趋势研究模式**：面向指定主题的中长期趋势洞察
- **Streamlit 可视化面板**：12 个 Tab 覆盖配置、运行、进度、收藏、检索、预览与排障
- **每日 0 点静默关键词维护**：LLM 批量标准化与趋势报告独立于主流程执行

---

## ✨ 核心功能

<table>
<tr>
<td colspan="2" align="center"><sub>— 数据获取 & 智能筛选 —</sub></td>
</tr>
<tr>
<td width="50%" valign="top">

### 📡 多数据源抓取

核心来源为 **ArXiv**（官方 API、完整分页、提交+更新双查询）与 PRL；其余来源（PRA/PRB、Nature、Science、Hugging Face Papers 等）为**默认关闭的声明式 JSON 定义**，面板下拉勾选即可启用。期刊论文若存在 ArXiv 版本，自动切换到 ArXiv 获取更完整摘要与 PDF。可选接入 **Semantic Scholar** 补充引用数与 AI TLDR。任一来源扫描失败即判定本次运行失败，水位线只在完整运行后推进。

</td>
<td width="50%" valign="top">

### 🎯 三种评分策略

`CHEAP_LLM` 对每篇论文按关键词逐项评分（0–10）：

- **v1 加权评分**：关键词权重总和 + 专家作者加分 + 动态及格线
- **核心相关度 V2**：面向核心相关性的改进评分
- **学习模式（`learned_preference_v1`）**：在 v1 之上叠加由收藏/不喜欢（强信号）与及格历史（弱信号）持续学习的关键词/作者修正库，单项限幅、整体衰减，学习影响始终低于直接配置的关键词

</td>
</tr>
<tr>
<td colspan="2" align="center"><sub>— 深度分析 & 知识积累 —</sub></td>
</tr>
<tr>
<td width="50%" valign="top">

### 🔍 深度 PDF 分析

通过筛选的论文自动下载 PDF，由 `SMART_LLM` 提取 **研究方法、创新点、技术栈、关键结论、局限性、研究关联、未来方向** 七个维度。支持 **PyMuPDF 本地解析（默认）**与 **MinerU 云端解析**双模式，MinerU 不可用时自动降级。报告中标注 TLDR 是否来自全文解析。

</td>
<td width="50%" valign="top">

### 📈 关键词趋势追踪

评分阶段提取的关键词写入 SQLite，**每天 0 点由独立 cron 任务静默完成 LLM 批量标准化**（同义归并、缩写展开、拼写统一），不占用主流程时间；按频率（每日/每周/每月）生成含彩色柱状图与趋势热图的独立 HTML 报告。

</td>
</tr>
<tr>
<td colspan="2" align="center"><sub>— 趋势研究 & 成本可观测 —</sub></td>
</tr>
<tr>
<td width="50%" valign="top">

### 🔬 趋势研究模式

独立的 `trend_research` 模式支持指定关键词、日期范围与 **ArXiv 全分类下拉过滤**（153 个一级分类按字母序），批量检索相关论文，逐篇生成 TLDR，并由 `SMART_LLM` 单次综合分析热点话题、时间演变、核心研究者、研究空白与方法论趋势。支持自定义深度分析提示词模板。

</td>
<td width="50%" valign="top">

### 📊 Token 消耗追踪

线程安全 Token 计数器统计各模型输入/输出消耗，持久化到 SQLite（成功/失败/中断的运行均保留）。「数据分析」页提供当日/近 30 天汇总、近一月热力图、静态自适应折线图与按模型汇总，数据永久保留。

</td>
</tr>
<tr>
<td colspan="2" align="center"><sub>— 报告输出 & 收藏反馈 —</sub></td>
</tr>
<tr>
<td width="50%" valign="top">

### 📄 双格式报告与收藏偏好

每日研究 / 趋势研究 / 关键词趋势三类报告，Markdown（适合归档）与 HTML（浏览器阅读、KaTeX 公式渲染）可独立开关。报告预览卡片内可直接 👍/👎 标记论文（无闪屏、实时落库）；「收藏与检索」页按时间列出收藏论文（标题超链接直达 arXiv）并统计收藏关键词与高产作者。

</td>
<td width="50%" valign="top">

### 🔎 论文全量检索

基于 SQLite 元数据的历史检索：标题/作者/摘要/TLDR/提取关键词匹配，支持来源、处理日期范围、最低总分、只看收藏过滤，分页浏览。数据永久存档，存量随时间增长也不怕找不到。

</td>
</tr>
<tr>
<td colspan="2" align="center"><sub>— 历史迁移 & 补跑 —</sub></td>
</tr>
<tr>
<td width="50%" valign="top">

### 📜 旧历史导入 + 时间段扫描

从 v3.x 升级时，「数据管理」页一键**读取旧历史**：v3.2 的历史 JSON 与全部 HTML 报告卡解析写入 SQLite（含评分、翻译、深度分析），重复分析以最新覆盖，缺失数据记入待补清单；随后自动按旧历史覆盖的日期范围**分块回扫 arXiv**，找出当年漏掉的论文。任务在空闲时自动排队执行，不打扰正在运行的每日研究；整批可重复执行且幂等。

</td>
<td width="50%" valign="top">

### 🧩 补充报告 + 过去日报

读取旧历史完成后，待补清单（缺失数据 + 遗漏论文）会自动汇总并重跑一次每日研究流程，生成格式与日报一致的**补充报告**（单次篇数受「本次最多处理论文数」约束）；「每日推送」页可选择一个过去**日期范围**，把每一天按队列顺序逐日补跑。报告时间戳为过去日期 + 本次运行时刻，与历史报告一起按时间线排列，都出现在「报告查看 → 每日研究」下。

</td>
</tr>
<tr>
<td colspan="2" align="center"><sub>— 配置管理 & 部署运维 —</sub></td>
</tr>
<tr>
<td width="50%" valign="top">

### 🧙 交互式配置向导 + 面板

首次部署可通过 6 步 CLI 向导完成初始化（Docker 首次部署自动触发）；日常使用 **Streamlit 面板**（12 Tab）调参、立即运行、查看进度与日志、预览报告。保存未浏览的页面时保留磁盘现值，不会误覆盖。

</td>
<td width="50%" valign="top">

### 🛡️ 生产级可靠性

**SQLite 持久化队列**（中断续跑、失败论文优先重试、单次处理上限防首次部署洪峰）、**原子交付**（报告落盘后单事务提交交付/通知/维护任务）、**共享 LLM 超时与重试策略**（单请求超时 + 指数退避 + 抖动 + Retry-After 遵从，认证类错误快速失败）、**arXiv 限流指数退避与跨领域冷却**、**无进展看门狗**、**文件锁防重并发**、**gzip 数据库备份**（本地全量按可配置保留天数自动清理，默认 7 天；填 0 永久保留；WebDAV 增量、远端永不删除）、**网络代理**（按服务粒度）。

</td>
</tr>
</table>

---

## 📑 导航目录

<table>
<tr>
<td width="50%" valign="top">

### 📘 快速上手

|           章节           | 简介                        |
| :----------------------: | :-------------------------- |
| [✨ 核心功能](#-核心功能) | 核心能力总览                |
| [🚀 快速开始](#-快速开始) | 三步完成首次运行            |
| [🛠️ 配置工具](#️-配置工具) | CLI 向导 + Streamlit 面板   |
| [🐳 部署方式](#-部署方式) | Docker / Actions / 本地定时 |

</td>
<td width="50%" valign="top">

### 📗 深入了解

|            章节            | 简介                         |
| :------------------------: | :--------------------------- |
|  [📖 功能详解](#-功能详解)  | 运行模式、报告、通知、锁机制 |
|  [📁 项目结构](#-项目结构)  | 目录与模块说明               |
|  [❓ 常见问题](#-常见问题)  | 11 个实战排障与深度使用指南  |
| [📝 更新日志](CHANGELOG.md) | 完整版本变更历史             |

</td>
</tr>
</table>

---

## 🚀 快速开始

### 第一步：克隆与安装

```bash
git clone https://github.com/yzr278892/arxiv-daily-researcher.git
cd arxiv-daily-researcher
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements-core.txt              # 面板额外需要 requirements-webui.txt
```

### 第二步：完成配置

推荐先运行交互式配置向导：

```bash
python src/utils/setup_wizard.py
```

向导会引导你完成 LLM 配置、搜索参数、数据源选择、关键词与研究背景、评分参数、通知渠道与高级设置，完成后自动生成 `.env` 与 `configs/config.json`（JSONC，支持手写注释，面板保存时会回注保留）。

> [!TIP]
> 若已有配置，向导会预填已有值；只需修改想变更的字段，其余按 Enter 保留。

<details>
<summary><b>手动配置（跳过向导）</b></summary>

**1）复制环境变量模板：**

```bash
cp .env.example .env
```

**2）填写 LLM：**

```env
CHEAP_LLM__API_KEY=sk-your-key
CHEAP_LLM__BASE_URL=https://api.openai.com/v1
CHEAP_LLM__MODEL_NAME=gpt-4o-mini

SMART_LLM__API_KEY=sk-your-key
SMART_LLM__BASE_URL=https://api.openai.com/v1
SMART_LLM__MODEL_NAME=gpt-4o
```

**3）填写核心关键词与领域：**

```jsonc
{
  "keywords": {
    "primary_keywords": {
      "weight": 1.0,
      "keywords": ["quantum error correction", "surface code"]
    },
    "research_context": "我的研究方向是容错量子计算与量子纠错码"
  },
  "target_domains": {
    "domains": ["quant-ph"]
  }
}
```

</details>

### 第三步：运行

```bash
# 每日研究模式（默认）
python main.py

# 趋势研究模式
python main.py --mode trend_research --keywords "quantum error correction"
```

运行结果默认输出到：
- 报告：`data/reports/`
- 日志：`logs/`

---

## 🛠️ 配置工具

本项目提供两种主要配置方式：**CLI 配置向导**与 **Streamlit 配置面板**。

### 🧙 交互式配置向导

适合首次部署、SSH 环境与无头服务器：

```bash
python src/utils/setup_wizard.py
```

| 步骤  | 内容     | 说明                                      |
| :---: | :------- | :---------------------------------------- |
|   1   | LLM 配置 | 选择 Provider、填写 API Key、可选连接测试 |
|   2   | 数据源   | ArXiv 与期刊启用、ArXiv 分类              |
|   3   | 关键词   | 主关键词、参考 PDF 提取、研究背景         |
|   4   | 评分     | 基础分、权重系数、作者加分                |
|   5   | 通知     | 渠道启用与凭据填写                        |
|   6   | 高级设置 | PDF 解析、并发、日志保留等                |

向导写入前会自动备份已有配置到 `.bak` 文件。

---

### 🖥️ Streamlit 配置面板

#### 启动方式

```bash
# 本地运行
streamlit run src/webui/config_panel.py
```

```bash
# Docker 运行
docker compose up -d config-panel
```

浏览器访问：`http://localhost:8501`（Docker 默认仅绑定 `127.0.0.1`）

配置面板与主程序共用同一套 `.env` 和 `configs/config.json`，修改后在下次任务运行时立即生效。侧边栏提供保存、从磁盘重新加载与 **🔄 重启主研究容器**（容器模式下经共享卷请求 worker 重启，重启后按最新配置重装 cron）。

#### 12 个 Tab 页详解

|   #   | Tab              | 功能                                                                                                                                                         |
| :---: | :--------------- | :----------------------------------------------------------------------------------------------------------------------------------------------------------- |
|   1   | **每日推送**     | 一键立即运行每日研究；其下是同级的 **过去日报**（选择日期范围、持久化入队并逐日补跑，按钮为「开始运行」）；再下方为**实时运行状态**（锁/PID + **阶段心跳进度**：准备→抓取→评分翻译→深度分析→生成报告，显示登记/已评分/已分析/失败计数与运行时长，5 秒自动刷新）；停止运行（二次确认）；**每日研究设置**（每日运行时间 / HTML 报告 / Markdown 报告 / 包含所有论文 / 单次处理上限）；运行日志查看器 |
|   2   | **报告查看**     | 三列展示每日研究 / 趋势研究 / 关键词趋势 HTML 报告，支持预览与前后日期导航；预览卡片内**随手标记**当日论文（喜欢/不喜欢，无闪屏实时落库）                     |
|   3   | **收藏与检索**   | **收藏的论文**（按时间列出、标题超链接直达 arXiv、👍/👎 统计）+ **关键词统计**（收藏论文关键词频次与高产作者）+ **论文检索**（全量历史元数据检索）             |
|   4   | **趋势分析**     | 设置关键词、日期范围、**ArXiv 全分类下拉过滤**、排序、最大结果数、TLDR、输出格式与综合分析技能；自定义深度分析提示词（可保存/应用/删除模板），一键启动 / 停止 |
|   5   | **关键词**       | 研究背景（置顶）、主关键词、参考 PDF 提取（高/中/低重要性三档权重框展示 + 已提取关键词只读列表）、相似度阈值                                                    |
|   6   | **数据源**       | 数据源开关、额外来源（启用后内置下拉多选 + 自定义新增）、**ArXiv 全分类下拉多选**与抓取超时                                                          |
|   7   | **评分（评价策略）** | 评分策略（v1 / V2 / 学习模式）、及格线公式、每关键词最高分、作者加分、学习库预览与实时评分预览                                                             |
|   8   | **数据分析**     | Token 用量（当日/近 30 天汇总、近一月热力图、静态自适应折线图、按模型汇总）、数据源健康（近 20 次扫描收据聚合）、精简运行诊断                                 |
|   9   | **通知**         | 全局开关、成功 / 失败 / 附件控制、六大渠道配置、SMTP 测试                                                                                                    |
|  10   | **数据管理**     | 一键导出配置文件（config.json + .env）为 zip；**WebDAV 同步**（手动 / 定时 / 报告后自动）；**数据库备份**（gzip 压缩；本地全量按可配置保留天数自动清理，默认 7 天，填 0 永久保留；WebDAV 增量上传远端永不删除 + 立即备份）；**旧历史导入**（读取旧历史 + 时间段扫描 + 生成补充报告，空闲时自动执行）         |
|  11   | **API**          | 配置 CHEAP_LLM / SMART_LLM / MinerU，支持连接测试                                                                                                            |
|  12   | **高级设置**     | PDF 解析模式（默认 pymupdf）、并发、Token 追踪、自动更新检查、关键词趋势追踪、重试、日志轮转与运行锁超龄回收、**网络代理**                                    |

### 🖼️ WebUI 界面预览

<table>
  <tr>
    <td align="center" width="50%">
      <img src="assets/img_en.png" alt="English WebUI" width="100%" />
      <br />
      <sub>英文 WebUI 主界面</sub>
    </td>
    <td align="center" width="50%">
      <img src="assets/img_noti.png" alt="Notification settings" width="100%" />
      <br />
      <sub>中文通知设置界面</sub>
    </td>
  </tr>
  <tr>
    <td align="center" width="50%">
      <img src="assets/img_prev.png" alt="Report preview" width="100%" />
      <br />
      <sub>中文报告预览界面</sub>
    </td>
    <td align="center" width="50%">
      <img src="assets/img_serh.png" alt="Search sources settings" width="100%" />
      <br />
      <sub>中文搜索源设置界面</sub>
    </td>
  </tr>
</table>

<details>
<summary><b>配置向导 vs 配置面板，该用哪个？</b></summary>

| 工具                             | 适用场景                    | 特点                                          |
| :------------------------------- | :-------------------------- | :-------------------------------------------- |
| **配置向导** (`setup_wizard.py`) | 首次部署、SSH、无浏览器环境 | CLI 交互、适合初始化、可连接测试              |
| **配置面板** (`config_panel.py`) | 日常调参、报告预览、排障    | 12 个 Tab，所见即所得，支持运行管理与趋势分析 |

**建议**：首次安装先跑向导，后续日常使用面板更高效。

</details>

---

## 🐳 部署方式

### Docker 部署 <sup>推荐</sup>

Docker 是**推荐部署方式**，适合长期后台运行。编排包含两个容器（单镜像双目标构建）：
- **arxiv-daily-researcher**（worker）：定时任务、触发监听、报告生成；`network_mode: host` 便于直接访问宿主机本地 LLM 服务
- **arxiv-daily-researcher-config-panel**（WebUI）：Streamlit 面板，仅绑定 `127.0.0.1:8501`，经共享卷与 worker 协作

#### 启动

```bash
git clone https://github.com/yzr278892/arxiv-daily-researcher.git
cd arxiv-daily-researcher
cp .env.example .env
docker compose up -d
```

容器内置三条定时任务（时区 `TZ`，默认 `Asia/Shanghai`）：

| 时间       | 任务                                       | 说明                                                         |
| :--------- | :----------------------------------------- | :----------------------------------------------------------- |
| 可配置     | 每日研究                                   | `configs/config.json` 的 `daily_research.run_time`（HH:MM，**默认 12:00**），面板「每日推送」页直接调整，容器重启后生效 |
| `0 0 * * *` | 关键词标准化 + 趋势报告                    | 静默执行，日志写入 `logs/keyword_*.log`，失败不影响日报      |
| 每分钟 tick | WebDAV 定时同步探测                        | 仅在配置选择定时模式且表达式匹配时真正传输                   |

其余默认行为：
- `RUN_ON_STARTUP=false`：主容器启动后不立即运行（需要可设为 `true`）
- `MODE=cron`：定时模式（`run-once` 为单次执行后退出）
- `SETUP_WIZARD=auto`：首次部署（无 `.env`）自动触发配置向导

> [!NOTE]
> 每日运行时间**不再由环境变量控制**：修改 `daily_research.run_time` 后点击侧边栏「🔄 重启主研究容器」（或 `docker compose restart arxiv-daily-researcher`）即可重装 cron。

#### 常用命令

```bash
# 查看运行状态
docker compose ps

# 查看日志
docker compose logs -f

# 启动 / 停止 WebUI
docker compose up -d config-panel
docker compose stop config-panel

# 容器内直接执行趋势研究
docker exec -it arxiv-daily-researcher python main.py --mode trend_research \
  --keywords "quantum error correction" \
  --date-from 2025-01-01 \
  --categories quant-ph

# 手动触发一次关键词维护（通常由每日 0 点 cron 自动执行）
docker exec arxiv-daily-researcher python -m modes.keyword_maintenance

# 停止主服务
docker compose down
```

#### WebUI 立即运行机制

WebUI 通过共享卷中的**原子 JSON 请求队列**请求主容器执行任务（无需 Docker Socket）：
1. 用户在 WebUI 点击「立即运行」，原子写入 `data/run/webui_triggers/<ts>_<id>.json`
2. 主容器 `entrypoint.sh` 的 `trigger_watcher` 每 5 秒轮询，`mv` 原子认领请求（防重复执行）
3. 认领后启动 `python main.py --mode daily_research`，真实 PID 写入 `data/run/webui_triggered.pid`
4. 运行日志写入 `logs/manual_*.log`；终态（含失败原因摘要）写入 `webui_triggers/status/`
5. 相同任务已在运行时触发改记为 `skipped_busy`（退出码 75），不伪装成功

重启按钮同样经共享卷投递 `restart_worker.request` 标记，worker 归档标记后向 PID 1 发送 TERM 完成重启。

<details>
<summary><b>容器环境变量</b></summary>

| 变量             | 默认值          | 说明                                               |
| :--------------- | :-------------- | :------------------------------------------------- |
| `TZ`             | `Asia/Shanghai` | 时区（影响所有 cron 时刻）                         |
| `RUN_ON_STARTUP` | `false`         | 启动时是否立即运行一次（默认否）                   |
| `MODE`           | `cron`          | `cron` 为定时模式，`run-once` 为单次执行           |
| `SETUP_WIZARD`   | `auto`          | `auto` 首次自动触发，`true` 强制触发，`false` 跳过 |

> 每日运行时间由 `configs/config.json` 的 `daily_research.run_time` 控制，没有环境变量覆盖项。

</details>

<details>
<summary><b>使用本地 LLM（Ollama 等）</b></summary>

由于主研究容器使用 `network_mode: host`，可以直接访问宿主机上的本地服务：

```env
CHEAP_LLM__API_KEY=ollama
CHEAP_LLM__BASE_URL=http://127.0.0.1:11434/v1
CHEAP_LLM__MODEL_NAME=qwen2.5:7b
```

</details>

---

### GitHub Actions 云端运行

适合没有常驻服务器的场景。支持两个工作流：
- `daily-run.yml`：每日研究
- `trend-research.yml`：手动趋势研究

> [!IMPORTANT]
> **使用建议**：GitHub Actions 适合简单使用或测试。请遵守 GitHub 使用规则，不要滥用 Actions 资源。`daily-run.yml` 中的定时触发默认是**注释掉的**，需要时再手动启用。**长期生产使用推荐 Docker 部署**。

#### 配置步骤

1. Fork 本仓库
2. 进入 **Settings → Secrets and variables → Actions**
3. 配置至少以下 Secrets：

| Secret 名称            | 必填  | 说明                         |
| :--------------------- | :---: | :--------------------------- |
| `CHEAP_LLM_API_KEY`    |   ✅   | 低成本 LLM API Key           |
| `CHEAP_LLM_BASE_URL`   |   ✅   | 低成本 LLM API 地址          |
| `CHEAP_LLM_MODEL_NAME` |   ✅   | 低成本 LLM 模型              |
| `SMART_LLM_API_KEY`    |   ✅   | 高性能 LLM API Key           |
| `SMART_LLM_BASE_URL`   |   ✅   | 高性能 LLM API 地址          |
| `SMART_LLM_MODEL_NAME` |   ✅   | 高性能 LLM 模型              |
| 通知相关 Secrets       | 可选  | SMTP / Telegram / Webhook 等 |

#### 手动趋势研究

`trend-research.yml` 支持传入 `keywords`、`date_from`、`date_to`、`categories`、`sort_order`、`max_results`。报告会作为 Artifact 保存 30 天。

---

### 本地定时运行（系统 Cron）

如果你不想使用 Docker 或 GitHub Actions，也可以直接使用系统 Cron：

```bash
crontab -e
# 每日研究（示例 12:00）+ 每日 0 点关键词维护
0 12 * * * cd /path/to/arxiv-daily-researcher && ./scripts/run_daily.sh >> /tmp/arxiv-cron.log 2>&1
0 0 * * * cd /path/to/arxiv-daily-researcher && PYTHONPATH=src python -m modes.keyword_maintenance >> /tmp/arxiv-keyword.log 2>&1
```

---

## 📖 功能详解

### 🔄 两种运行模式

| 维度     | `daily_research`（默认）       | `trend_research`               |
| :------- | :----------------------------- | :----------------------------- |
| 定位     | 每日自动追踪最新论文           | 指定主题的长期趋势分析         |
| 数据源   | ArXiv + 声明式期刊源           | ArXiv                          |
| 时间范围 | 固定最近 3 天（+公告延迟回看；过去日期可补跑） | 任意日期区间                   |
| 筛选方式 | 关键词加权评分（三种策略）     | 无评分，全量保留               |
| 核心分析 | 高分论文 PDF 深度分析          | 全量 TLDR + 趋势综合分析       |
| 触发方式 | Cron / Docker / Actions / 面板 | CLI / 面板 / Actions           |
| 输出路径 | `data/reports/daily_research/` | `data/reports/trend_research/` |

### 📅 每日研究流水线

```text
1. 准备关键词与动态及格线
2. 从 ArXiv / 期刊抓取论文（候选先原子登记进 SQLite 队列）
3. 按队列逐篇评分 + 翻译（跳过已交付的精确版本；新版本重新处理）
4. 对通过筛选的论文执行 PDF 深度分析
5. 生成 Markdown / HTML 报告并原子提交交付状态
6. 发送通知、执行数据库备份与 WebDAV 维护
```

每个阶段切换都会写入阶段心跳，「每日推送」页实时显示当前阶段（准备/抓取/评分翻译/深度分析/生成报告）与登记/已评分/已分析/失败计数。日报固定回看**最近 3 天**并处理窗口内全部新论文（失败后水位线自动扩窗重扫，已交付版本由账本去重）；更早日期的新论文通过「过去日报」补跑。单次处理上限 `max_papers_per_run`（默认 200，`0` 不限）防止首次部署的历史论文一次涌入；超出部分留队，失败论文下次优先；补充报告同样受此上限约束。

### 🧩 补跑与历史迁移

- **旧历史导入 + 自动补充报告**（`--mode legacy_import`，面板「数据管理」一键触发）：解析 v3.2 历史 JSON 与全部 HTML 报告写入 SQLite，随后按旧历史日期范围分块回扫 arXiv 找遗漏论文；缺失数据与遗漏论文会在同一工作流中自动衔接一次补充报告。成功交付的条目自动出账，失败项会在下次读取旧历史时重试。
- **过去日报**（`--mode backfill_run --date-from YYYY-MM-DD --date-to YYYY-MM-DD`）：所选范围的每个日期持久化入队，按日期顺序逐日运行当天的完整每日研究；单天失败会保留记录但不会阻塞后续日期。报告文件名时间戳 = 过去日期 + 本次运行时分秒，与历史真实报告一起按时间线排列。
- 上述工作流均在空闲时执行（等待每日研究、趋势分析及维护任务结束后才启动），互不冲突、可恢复、可重跑。

### 🎯 动态及格线公式

```text
及格线 = base_score + weight_coefficient × Σ(关键词权重)
```

默认配置中 `base_score = 1.5`、`weight_coefficient = 2.5`，可在「每日推送」Tab 的「每日研究设置」或 `configs/config.json` 中调整。

### 🛡️ LLM 与 arXiv 的重试回退

所有 OpenAI 客户端共享统一的超时/重试边界（`configs/config.json` 的 `llm` 段）：

| 配置                          | 默认  | 说明                                                       |
| :---------------------------- | :----: | :--------------------------------------------------------- |
| `llm.timeout_seconds`         |  300   | 单次 LLM HTTP 请求超时                                     |
| `llm.sdk_max_retries`         |   1    | SDK 层快速重试（连接抖动 / Retry-After）                   |
| `llm.retry_max_attempts`      |   5    | 应用层最大尝试次数                                         |
| `llm.retry_min_wait`          |   5    | 指数退避起始等待（秒，带抖动）                             |
| `llm.retry_max_wait`          |  120   | 指数退避等待上限（秒）                                     |

429/5xx/超时/空响应按瞬态错误指数退避重试；401/403/404/400 等认证或参数错误快速失败，不浪费时间。另有全局请求池限速（`llm_request_pool.requests_per_minute`，默认 30）适配低并发中转。

arXiv 抓取侧：无进展看门狗（默认 180s，持续收到结果则合法延长）、429 指数退避（60→480s）、其他错误线性退避、**遵从响应头 Retry-After**、领域失败后跨领域冷却 60s；关键词搜索与领域扫描共享同一套策略。

### 📡 数据源与 ArXiv 优先策略

- ArXiv：官方 `arxiv` 库，分页间 6 秒限速，提交+更新双查询完整分页
- 期刊：通过 OpenAlex 获取，声明式定义可在面板启用/自定义
- 期刊论文存在 ArXiv 版本时优先切换到 ArXiv 元数据与 PDF
- 可选接入 Semantic Scholar 获取引用数与 AI TLDR

### 🔍 PDF 解析与智能降级

| 模式      | 优点                         | 限制                |
| :-------- | :--------------------------- | :------------------ |
| `pymupdf` | 纯本地、零外部依赖（**默认**） | 解析质量受 PDF 影响 |
| `mineru`  | 结构化效果更好，适合复杂论文 | 需要 Token          |

当 MinerU 不可用时，系统自动降级到 PyMuPDF，避免整次任务失败。

### 🔒 并发运行互斥锁

| 模式             | 锁文件                                 |
| :--------------- | :------------------------------------- |
| `daily_research` | `data/run/daily_research.lock`         |
| `trend_research` | `data/run/trend_research_<hash8>.lock` |

相同任务重复启动时直接安全退出；锁文件写入 PID 与启动时间；支持超龄锁回收（默认 12 小时）；回收失败时保守退出，避免双实例并发。

### 📄 报告系统

| 报告       | 路径                                                    |
| :--------- | :------------------------------------------------------ |
| 每日研究   | `data/reports/daily_research/{markdown,html}/<source>/` |
| 趋势研究   | `data/reports/trend_research/{markdown,html}/<slug>/`（含 metadata.json） |
| 关键词趋势 | `data/reports/keyword_trend/{markdown,html}/`           |

Markdown / HTML 可独立开关（「每日推送」与「趋势分析」Tab）。每日报告通常包括统计摘要、通过论文详情（含深度分析与全文 TLDR 溯源）、未通过论文列表、关键词趋势图与 Token 消耗统计。

### 🔔 通知系统

支持 **邮件、企业微信、钉钉、Telegram、Slack、通用 Webhook** 六渠道。通知开关分为全局总开关与各渠道独立开关两层；渠道只有在配置已填写且 `enabled=true` 时才会真正发送。通知走 SQLite outbox：失败自动保留待补发，不会因渠道抖动丢消息。

---

## 📁 项目结构

```text
arxiv-daily-researcher/
├── main.py                          # CLI 入口，按模式分发
├── .env.example                     # 环境变量模板
├── requirements-core.txt            # worker 依赖（requirements-webui.txt 为面板依赖）
├── README.md / README_EN.md
│
├── src/
│   ├── config.py                    # 全局配置加载（.env + JSONC config.json）
│   ├── scoring_policy.py            # 评分策略（v1 / core_v2 / learned_preference_v1）
│   ├── modes/
│   │   ├── daily_research.py        # 每日研究流水线
│   │   ├── trend_research.py        # 趋势研究流水线
│   │   └── keyword_maintenance.py   # 每日 0 点静默关键词标准化任务
│   ├── agents/                      # LLM 分析相关 Agent
│   ├── sources/                     # ArXiv / OpenAlex / HF Papers / 搜索编排
│   ├── report/                      # daily / trend / keyword_trend 报告生成
│   ├── notifications/               # 多渠道通知
│   ├── parsers/                     # PDF 解析（PyMuPDF / MinerU）
│   ├── keyword_tracker/             # 关键词追踪与标准化
│   ├── utils/
│   │   ├── config_io.py             # JSONC 读写（保留手写注释）
│   │   ├── daily_research_store.py  # SQLite 状态库（队列/交付/偏好/用量）
│   │   ├── llm_resilience.py        # 共享 LLM 超时与重试策略
│   │   ├── llm_request_pool.py      # 全局 LLM 请求限速
│   │   ├── run_lock.py / webui_trigger.py / backup.py / webdav_sync.py …
│   └── webui/                       # Streamlit 配置面板
│       ├── config_panel.py
│       ├── i18n.py                  # 中英双语
│       ├── arxiv_categories.py      # 153 个 ArXiv 一级分类目录
│       ├── report_component/        # 报告预览自定义组件（无闪屏标记）
│       └── tabs/                    # 12 个 Tab 页模块
│
├── configs/
│   ├── config.json                  # 主配置文件（JSONC）
│   └── templates/                   # 报告、通知、邮件模板
│
├── docker-compose.yml               # 双容器编排（worker + config-panel）
├── docker/
│   ├── Dockerfile                   # 多阶段：worker / webui 两个目标
│   └── entrypoint.sh                # cron 安装 / 触发监听 / 重启处理
│
├── VERSION                          # 版本号（用于更新检查）
├── scripts/                         # 运行脚本与 Makefile
├── assets/                          # README / WebUI 预览图片
├── data/                            # 运行数据（SQLite、报告、触发队列；自动创建）
└── logs/                            # 系统日志与每次运行日志
```

---

## ❓ 常见问题

<details>
<summary><b>1. WebDAV 连接坚果云总是提示失败（403）怎么办？</b></summary>

坚果云 WebDAV 服务器**不支持 HTTP HEAD 方法**，而大多数 WebDAV 客户端库使用 HEAD 来检测资源是否存在。本项目已内置兼容处理（使用 PROPFIND 替代 HEAD 进行存在性检查）。

如果仍遇到连接问题，请检查：
- WebDAV URL 是否以 `https://dav.jianguoyun.com/dav/` 结尾
- 密码是否为坚果云的**应用专用密码**（在坚果云账户安全设置中生成，而非登录密码）
- 在 WebUI「数据管理」Tab 中点击「测试连接」确认凭据有效
</details>

<details>
<summary><b>2. 趋势分析如何选择合适的参数？</b></summary>

几个关键建议：
- **日期范围**：初次使用建议 90-180 天，避免范围过大导致结果过多
- **分类过滤**：使用全分类下拉限定到相关领域（如 `quant-ph · Quantum Physics`），大幅提升精度
- **输出格式**：Markdown 和 HTML 均可独立开关，在 WebUI 趋势分析 Tab 中直接切换
- **Skill 选择**：默认 `comprehensive_analysis` 单次覆盖全部五个维度，适合多数场景
- **max_results**：默认 500，如果结果很多但分析速度慢，可以降低到 200；反之可以提升到 1000
</details>

<details>
<summary><b>3. 任务提示"已在运行中"，但我怀疑是残留锁怎么办？</b></summary>

系统已支持多层保护：
- **死进程残留锁自动清理**：启动时检查 PID 是否存活，不存活则自动回收
- **超龄锁自动回收**：超过 `run_lock_max_age_hours`（默认 12 小时）的锁会被回收
- **面板停止**：「每日推送」页运行状态区的停止按钮（二次确认），已完成阶段保留、未完成论文留队

> [!WARNING]
> 仅在不确定 PID 是否存活时才手动清理锁。如果进程确实在运行，删除锁可能导致重复运行。
</details>

<details>
<summary><b>4. Docker 中如何配置和使用本地 LLM（Ollama / vLLM / LocalAI）？</b></summary>

主研究容器默认使用 `network_mode: host`，因此可以直接访问宿主机上的本地 LLM 服务：

```env
CHEAP_LLM__API_KEY=ollama
CHEAP_LLM__BASE_URL=http://127.0.0.1:11434/v1
CHEAP_LLM__MODEL_NAME=qwen2.5:7b
```

如果使用桥接网络模式（WebUI 容器等），需要将 `127.0.0.1` 换成 `host.docker.internal`（Windows/Mac）或宿主机真实 IP（Linux）。确保本地 LLM 服务已监听 `0.0.0.0` 而非 `127.0.0.1`。
</details>

<details>
<summary><b>5. WebUI 的「立即运行」与「重启容器」是如何与主容器协同的？</b></summary>

Docker 模式下采用**共享卷消息机制**，无需 Docker Socket：

- **立即运行**：WebUI 原子写入 `data/run/webui_triggers/<ts>_<id>.json` → 主容器 `trigger_watcher` 每 5 秒轮询并 `mv` 原子认领 → 启动 `python main.py --mode daily_research`（PID 写入 `webui_triggered.pid`，日志写入 `logs/manual_*.log`）
- **重启容器**：侧边栏「🔄 重启主研究容器」写入 `restart_worker.request` → worker 归档标记后向 PID 1 发送 TERM → 容器重启并按最新配置重装 cron
- **停止运行**：面板经共享卷向真实 PID 转发 SIGTERM，未完成论文留队待重试

关键是两个容器必须挂载**相同的** `data/` 和 `logs/` 卷。
</details>

<details>
<summary><b>6. 如何配置网络代理？代理可以按服务粒度控制吗？</b></summary>

在 WebUI「高级设置」页的网络代理分区或 `configs/config.json` 的 `proxy` 块中配置：

- **全局开关**：`proxy.enabled`
- **代理地址**：`proxy.url`，支持 HTTP/SOCKS5（如 `http://127.0.0.1:7890`）
- **服务粒度控制**（`proxy.scope`）：可独立控制 ArXiv、OpenAlex、Semantic Scholar、LLM API、通知、检查更新是否走代理

Docker 注意：`network_mode: host` 模式下用 `127.0.0.1`；桥接模式下 Linux 需 `--add-host=host.docker.internal:host-gateway`。
</details>

<details>
<summary><b>7. Docker 报错 <code>NameResolutionError</code> 或无法解析 <code>export.arxiv.org</code> 怎么办？</b></summary>

这通常是 **NAS/宿主机网络、Docker DNS 或 Tailscale DNS 在网络切换后未刷新**，不是论文抓取或项目业务代码错误。典型日志为：

```text
HTTPSConnectionPool(host='export.arxiv.org', ...)
... NameResolutionError: Temporary failure in name resolution
```

先在宿主机和 worker 中分别确认解析是否正常：

```bash
getent hosts export.arxiv.org
docker exec arxiv-daily-researcher getent hosts export.arxiv.org
docker exec arxiv-daily-researcher cat /etc/resolv.conf
```

移动 NAS、重配路由/DHCP、切换网络或启停 Tailscale 后，优先修复 NAS 的上游 DNS，再重新创建应用容器以让 Docker 重建容器内的 `resolv.conf`：

```bash
docker compose up -d --force-recreate
```

若 NAS 的 DHCP DNS 经常不稳定，可在**本机**新增 `docker-compose.override.yml`（避免把某个地区/个人网络的 DNS 固化到项目默认编排）并重新创建容器：

```yaml
services:
  arxiv-daily-researcher:
    dns:
      # 仅在已启用 Tailscale MagicDNS 时保留这一项
      - 100.100.100.100
      # 中国大陆网络可用的公共 DNS 备用项；其他地区请改为当地可靠 DNS
      - 223.5.5.5
  config-panel:
    dns:
      - 100.100.100.100
      - 223.5.5.5
```

`100.100.100.100` 是 Tailscale 的 MagicDNS 地址，并非通用公共 DNS；未使用 MagicDNS 时应删除它并配置两台适合本地网络的公共/路由器 DNS。项目会对网络请求重试，但当容器没有任何可用 DNS 时，必须由部署环境恢复解析能力。
</details>

<details>
<summary><b>8. Markdown 和 HTML 报告有什么区别？可以只生成一种吗？</b></summary>

两者内容相同，格式不同：**Markdown** 适合 Git 版本管理、归档；**HTML** 适合浏览器阅读、分享，含样式与 KaTeX 公式渲染。在「每日推送」Tab 的「每日研究设置」中可独立开关；趋势分析报告的开关在「趋势分析」Tab。
</details>

<details>
<summary><b>9. 关键词追踪与标准化是什么时候运行的？</b></summary>

1. CHEAP_LLM 在评分阶段自动从论文标题、摘要中提取关键词并写入 SQLite
2. **每天 0 点**由独立 cron 任务（`modes/keyword_maintenance.py`）静默执行 LLM 批量语义归并（如 "quantum computing" 和 "quantum computation" 合并），失败只记日志、次日自动重试，不影响日报
3. 按配置频率（每日/每周/每月/始终）生成关键词趋势报告

可在「高级设置」Tab 或 `config.json` 中将 `keyword_tracker.enabled` 设为 `false` 关闭（`keyword_tracker.normalization.enabled` 可单独关闭标准化）。
</details>

<details>
<summary><b>10. MinerU PDF 解析和 PyMuPDF 如何选择？MinerU Token 过期了怎么办？</b></summary>

| 场景                           | 推荐模式  |
| :----------------------------- | :-------- |
| 无外部依赖、离线环境、长期稳定 | `pymupdf`（默认） |
| 追求解析质量、处理复杂排版论文 | `mineru`  |

在 WebUI「API」Tab 或「高级设置」Tab 切换。MinerU Token 有效期 3 个月，过期后系统**自动降级**到 PyMuPDF 不会中断运行，并发送错误告警通知；到 [mineru.net](https://mineru.net/apiManage/apiKey) 重新申请 Token 更新即可。
</details>

<details>
<summary><b>11. 如何利用 WebDAV 同步在多台设备间共享配置和报告？</b></summary>

WebDAV 同步支持三种模式（在 WebUI「数据管理」Tab 配置）：

| 模式           | 说明                                   |
| :------------- | :------------------------------------- |
| **手动**       | 在 WebUI 中点击「上传」或「下载」按钮  |
| **定时**       | 按 cron 表达式自动执行（如每天 23:00） |
| **报告后自动** | 每次每日研究报告生成后自动上传         |

同步范围可选：配置文件（config.json）、历史记录、关键词数据、报告文件。默认仅同步配置文件。典型用法：主设备设置「报告后自动」上传，辅设备设置「手动」模式，按需下载恢复配置和数据。
</details>

---

## 📜 许可证

本项目采用 [AGPL-3.0](https://www.gnu.org/licenses/agpl-3.0.html) 许可证。

| 条款       | 说明                                     |
| :--------- | :--------------------------------------- |
| ✅ 使用     | 可自由使用、修改、分发                   |
| ✅ 商用     | 允许商业使用                             |
| 📋 源码公开 | 修改后的版本须公开源代码并使用相同许可证 |
| 🌐 网络使用 | 通过网络提供服务时也须公开源代码         |
| 📝 声明     | 需保留原始版权声明和许可证               |

---

## 💬 社区与反馈

项目持续活跃开发中。欢迎通过以下方式参与：

- **🐛 报告问题**：[GitHub Issues](https://github.com/yzr278892/arxiv-daily-researcher/issues) — 遇到 Bug 或有功能建议，欢迎提交 Issue
- **🔀 贡献代码**：Fork → 修改 → Pull Request，我们欢迎任何改进
- **⭐ Star**：如果这个项目对你有帮助，点亮 Star 是对我们最大的鼓励

---

## 🤝 API 使用说明

本项目遵循各 API 提供方的使用规范，确保合规调用：

| API                  | 合规措施                                                                |
| :------------------- | :---------------------------------------------------------------------- |
| **ArXiv**            | 使用官方 `arxiv` Python 库，内置 6 秒请求延迟、限流指数退避与 Retry-After 遵从 |
| **OpenAlex**         | 请求头包含联系方式，建议配置 `OPENALEX_EMAIL` 进入礼貌池（Polite Pool） |
| **Semantic Scholar** | 请求头含 User-Agent，支持配置 API Key 获取更高速率                      |
| **MinerU**           | 遵守每日 2000 页优先级额度限制，超出后自动降至普通优先级                |

> [!NOTE]
> 所有外部 API 与 LLM 调用均配有指数退避自动重试（带抖动）与全局请求限速，网络波动与供应商限流不会导致运行中断；认证类错误快速失败并明确记录。

---

## 🙏 致谢

- 感谢 [Claude](https://www.anthropic.com/claude) 与 [Claude Code](https://claude.ai/code) 在本项目开发过程中的辅助
- 感谢 [ArXiv](https://arxiv.org/)、[OpenAlex](https://openalex.org/)、[Semantic Scholar](https://www.semanticscholar.org/) 提供开放学术数据
- 感谢 [MinerU](https://mineru.net/) 提供云端 PDF 解析能力

---

## 📝 更新日志

完整的版本变更历史请查看 **[CHANGELOG.md](CHANGELOG.md)**。

### 最新版本摘要

<table>
<tr><th>版本</th><th>日期</th><th>类型</th><th>亮点</th></tr>
<tr><td><b>v4.0</b></td><td>2026-08-23</td><td>🚀 重大更新</td><td>SQLite 日报历史与精确版本交付、持久化待处理队列、arXiv 完整分页与扫描收据、声明式额外数据源、学习模式评分、gzip 数据库备份（本地全量按可配置保留天数自动清理，默认 7 天；填 0 永久保留；WebDAV 增量永不删除）、论文全量检索、收藏与检索独立 Tab（时间轴收藏列表 + arXiv 超链接 + 关键词统计）、ArXiv 全分类下拉、每日运行时间面板化（默认 12:00，纯配置无环境变量）、关键词标准化改为每日 0 点静默任务、长任务阶段进度反馈、共享 LLM 超时/重试强化与 arXiv 退避统一（Retry-After + 跨领域冷却）、侧边栏一键重启 worker、实时运行监控与停止控制、v3.2 旧历史一键导入 + 时间段遗漏扫描 + 补充报告、过去日期日报补跑（报告按文件名时间戳排序）、日报固定 3 天窗口（移除搜索天数配置）、Docker 镜像拆分与安全修复、大规模可靠性加固（fail-closed、原子交付、边界加固）</td></tr>
<tr><td><b>v3.2</b></td><td>2026-04-26</td><td>✨ 增强 + 🐛 修复</td><td>网络代理（per-service 粒度）、WebDAV 数据同步（含坚果云兼容修复）、配置一键导出、Docker 更新通知、Markdown/HTML 报告独立开关、趋势分析双开关输出、ArXiv 抓取优化与早停、每日深度分析可配置</td></tr>
<tr><td><b>v3.1</b></td><td>2026-04-15</td><td>✨ 增强 + 🐛 修复</td><td>运行管理 Tab、日志查看器升级、趋势分析 Tab、报告查看增强、ArXiv 超时守卫、运行锁超龄回收</td></tr>
<tr><td><b>v3.0</b></td><td>2026-03-09</td><td>✨ 重大更新</td><td>研究趋势模式、趋势分析 GitHub Actions 工作流、综合趋势分析、Token 追踪、配置向导自动触发、并发运行互斥锁、运行专用日志、Streamlit 配置面板（含报告查看）、关键词趋势 HTML 报告</td></tr>
</table>

[查看完整更新历史 →](CHANGELOG.md)

---

<div align="center">

如果这个项目对你有帮助，欢迎点一个 **Star** ⭐

[![Star History Chart](https://api.star-history.com/svg?repos=yzr278892/arxiv-daily-researcher&type=Date)](https://star-history.com/#/yzr278892/arxiv-daily-researcher&Date)

[![Issues](https://img.shields.io/github/issues/yzr278892/arxiv-daily-researcher?style=flat-square&label=Issues)](https://github.com/yzr278892/arxiv-daily-researcher/issues)
[![Email](https://img.shields.io/badge/Email-联系作者-blue?style=flat-square)](mailto:yzr278892@gmail.com)

</div>
