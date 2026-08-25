<div align="center">

# 🔬 ArXiv Daily Researcher

**面向个人研究者的可恢复论文研究工作流：抓取、筛选、分析、报告、归档与通知。**

[![Version](https://img.shields.io/badge/version-v4.0-2563eb?style=flat-square)](CHANGELOG.md)
[![License](https://img.shields.io/badge/license-AGPL--3.0-16a34a?style=flat-square)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12+-facc15?style=flat-square)](https://www.python.org/)
[![Docker](https://img.shields.io/badge/docker-compose-2496ed?style=flat-square)](docker-compose.yml)

[English](README_EN.md) · [更新日志](CHANGELOG.md) · [问题反馈](https://github.com/yzr278892/arxiv-daily-researcher/issues)

</div>

> [!IMPORTANT]
> v4.0 的唯一日常历史系统是 SQLite。`data/history/*_history.json` 不再参与每日运行、同步或双写；它仅作为 v3.2 旧历史导入功能的输入。

ArXiv Daily Researcher 不只是“每天抓几篇论文”。它把一次研究任务拆为可审计、可恢复的状态：先完整扫描并登记候选，再按规则评分、翻译与分析，确认报告已落盘后才提交交付记录和通知。网络、LLM、通知或 WebDAV 的单点波动不会让已经完成的论文重复推送，也不会静默丢掉未完成项。

## ✨ v4.0 一览

| 你关心的事 | v4.0 的处理方式 |
| :-- | :-- |
| 每天的新论文会漏吗？ | 每日报固定回看最近 3 天；arXiv 同时扫描提交和更新并完整分页。扫描收据与水位线让失败窗口在下一次自动扩大恢复。 |
| 一次论文太多怎么办？ | 所有候选先写入 SQLite；“本次最多处理论文数”只限制评分/分析，不截断抓取。`0` 表示不限量，剩余项会持久排队。 |
| 同一篇论文重跑会重复吗？ | 以 `(source, canonical_id, version)` 精确去重；arXiv 新版本是新的可交付版本，旧版本不会被覆盖。 |
| LLM 或 PDF 解析失败怎么办？ | 成功阶段保留，失败阶段带安全错误信息留在待重试队列；不把不完整论文交付为日报。 |
| v3.2 历史如何迁入？ | 一键读取 JSON 与 HTML，等待其他任务空闲后执行；最新分析优先、缺失可重试、自动时间段回扫与补充报告。 |
| 过去某段时间的日报如何补？ | 日期范围进入持久队列，按天逐篇运行完整流程；同一天超过上限会自动续跑。 |
| 怎么保障数据和通知？ | SQLite 一致性 gzip 备份、本地可配置保留期、WebDAV 增量归档、SQLite notification outbox 与多平台结果通知。 |

## 📑 导航

- [快速开始](#-快速开始)
- [运行模型：从扫描到交付](#-运行模型从扫描到交付)
- [WebUI 与截图](#-webui-与截图)
- [数据源、评分与分析](#-数据源评分与分析)
- [历史导入、补充报告与过去日报](#-历史导入补充报告与过去日报)
- [通知、备份与可观测性](#-通知备份与可观测性)
- [部署、升级与命令行](#-部署升级与命令行)
- [复杂问题排查](#-复杂问题排查)

## 🚀 快速开始

### Docker（推荐）

需要 Docker Engine 与 Docker Compose v2。项目会启动两个容器：长期运行的 worker（cron、队列监听、研究任务）与只绑定本机的 Streamlit WebUI。

```bash
git clone https://github.com/yzr278892/arxiv-daily-researcher.git
cd arxiv-daily-researcher

cp .env.example .env
# 至少填写 CHEAP_LLM 与 SMART_LLM 的 API Key / Base URL / Model

docker compose up -d --build
docker compose ps
```

打开 <http://127.0.0.1:8503>，在 WebUI 完成其余配置。面板默认只监听本机；如需远程访问，请放在带认证的反向代理或 VPN 后面，而不是直接暴露端口。

首次建议做一个小规模验证：在「每日推送 → 每日研究设置」把“本次最多处理论文数”临时设为 `5`，点击“立即运行”，确认报告、SQLite、通知和日志都符合预期后再改回 `0`（处理全部）或你的日常上限。

### 本地运行

```bash
python -m venv venv
source venv/bin/activate                 # Windows: venv\Scripts\activate
pip install -r requirements-core.txt
pip install -r requirements-webui.txt    # 需要 WebUI 时

cp .env.example .env
python main.py
```

也可使用 `scripts/run_daily.sh`、`scripts/run_daily.ps1` 或 `scripts/run_daily_mac.sh`。本地定时任务和 WebUI 配置方式见后文；生产自托管更建议使用 Compose，以免 worker、日志与共享队列的路径不一致。

## 🧭 运行模型：从扫描到交付

### 每日研究

默认工作流固定回看最近 3 天；它不再提供“搜索最近 N 天”配置。需要处理更早日期时，请使用“过去日报”队列，而不是把日常扫描窗口无限扩大。

1. **准备与互斥**：取得运行锁，等待旧历史导入等独占任务结束，加载关键词、数据源和上次成功扫描水位线。
2. **完整扫描并登记**：arXiv 按分类查询首次提交和最后更新，完整分页；额外来源也写入终态扫描收据。所有候选先进入 SQLite。
3. **恢复优先队列**：失败或缺失阶段的论文优先于普通新候选。正数上限只限制本轮下游处理量；未处理项不丢失。
4. **评分与内容处理**：按策略筛选，生成中文摘要；对需要深度分析且可获取 PDF 的合格论文做 PDF 解析和 SMART_LLM 分析。
5. **报告与原子交付**：报告文件存在且非空后，论文交付账本、运行状态、通知 outbox、扫描水位线和后续维护项一起提交。通知或 WebDAV 短暂失败不会把论文重新变成“新论文”。
6. **后处理**：自动备份 SQLite、按配置触发 WebDAV 增量同步、补发旧通知，并更新数据分析页的用量/健康信息。

```text
完整扫描 → SQLite 候选队列 → 评分/翻译/分析 → 报告落盘
    ↑             │                    │                 │
    └──── 水位线恢复 └── 失败留队重试 ───┴── 原子交付 + 通知 outbox
```

### 三类报告

| 报告 | 触发方式 | 时间与排序 |
| :-- | :-- | :-- |
| 每日研究 | cron、WebUI“立即运行”或 CLI | 当前运行时间；按文件名时间戳排序。 |
| 补充报告 | 旧历史导入发现缺数据/遗漏后自动衔接 | 格式与日报相同，标题标注“补充报告”；受本次处理上限约束。 |
| 过去日报 | WebUI 日期范围或 `backfill_run` | 每个目标日期单独入队；文件名使用“过去日期 + 实际运行时分秒”。 |

报告默认输出 HTML 与 Markdown，可分别关闭。每日研究报告位于 `data/reports/daily_research/{html,markdown}/<source>/`；趋势研究与关键词趋势报告分别位于 `trend_research/` 和 `keyword_trend/`。

## 🖥️ WebUI 与截图

WebUI 提供 12 个页签：每日推送、报告查看、收藏与检索、趋势分析、关键词、数据源、评分、数据分析、通知、数据管理、API、高级设置。保存时会保留未浏览页签的磁盘配置，不会用默认值覆盖它们。

### 每日推送：启动、状态、队列与过去日报

![每日推送状态与过去日报队列](assets/webui_daily_push_v4.png)

状态面板只在有任务运行时自动刷新，显示阶段心跳、队列与日志尾部；可请求停止 WebUI 触发的任务，已完成阶段保留、未完成论文继续待重试。过去日报支持开始/结束日期，不是单日按钮。

### 数据分析：用量、LLM 健康、来源健康与运行诊断

![数据分析中的 LLM 健康面板](assets/webui_analytics_v4.png)

LLM 健康面板不发“探针”请求，也不额外消耗 token；它仅汇总真实任务的最终调用结果。CHEAP_LLM 和 SMART_LLM 分别显示最近调用、连续失败、近 20 次成功率、最近成功时间和脱敏后的失败说明。历史 token 用量、数据源扫描收据与运行诊断同在此页。

### 评分策略

![中文评分策略与策略说明](assets/webui_scoring_v4.png)

策略名称、说明和选项都支持中英双语；保存到配置文件的仍是稳定的策略 ID，因此切换语言不会改变实际行为。

### 数据管理与旧历史导入

![数据库备份设置](assets/webui_data_management_v4.png)

![旧版本历史导入入口](assets/webui_history_import_v4.png)

所有截图均在本地最新 WebUI 生成，未展示 API Key、密码、Webhook、邮箱、内网地址或本机路径。

## 📡 数据源、评分与分析

### 数据源

- **arXiv**：默认主来源。开关开启后配置目标分类、抓取超时与公告延迟重扫；内置 153 个一级分类的可搜索多选。
- **额外来源**：独立滑动开关。启用后再展开内置来源（PRL、PRA/PRB、Nature/Science、Hugging Face Papers 等）与安全表单化的 OpenAlex 期刊定义。来源定义只允许数据字段，不执行用户粘贴的 Python、import path 或 callback。
- **OpenAlex**：只有启用额外期刊来源时才调用。可选 API Key 用于更高的官方配额；旧的联系邮箱配置已移除。
- **Semantic Scholar**：可选 TL;DR/引用信息增强，关闭后不会请求它。它是增强服务，不取代 arXiv 的完整分类扫描。

WebUI 的 API 页为 OpenAlex、Semantic Scholar 与 MinerU 分别提供开关、连接测试和官方控制台链接。供应商配额和政策会变化，部署前应以其官方页面为准；本项目会限速、重试并在认证/参数错误时快速失败。

### 三种评分策略

| 策略 | 资格判断 | 排序与适用场景 |
| :-- | :-- | :-- |
| **核心相关性 V2** | 主关键词加权平均相关度达到阈值，且至少一个主关键词强匹配。参考词和作者偏好不能让无关论文通过。 | 推荐新配置。参考关键词、专家作者可作为额外排序信号。 |
| **加权关键词 V1（兼容）** | 主关键词/参考关键词相关度和作者加分按权重累计，与动态通过线比较。 | 适合延续旧报告或依赖参考关键词的配置。 |
| **偏好学习 V1** | 以 V1 资格逻辑为基础。 | 收藏/不喜欢和既有 V1 通过记录形成受限、衰减的关键词/作者偏好，用于微调排序；直接配置的关键词始终优先。 |

评分结果带有非敏感审计信息。报告内可一键 👍 / 👎；标记写入 SQLite，清除标记也保留历史状态。收藏与检索页提供论文全库搜索、收藏时间线、关键词统计与作者 Top 列表；长列表超过 10 条使用原生滚动容器。

### LLM、PDF 与关键词

- `CHEAP_LLM` 负责初筛、关键词、翻译和 TLDR；`SMART_LLM` 负责深度分析与趋势总结。两者通过 OpenAI 兼容接口配置，可使用云模型、中转或本地兼容服务。
- 所有 LLM 客户端共享请求池、超时和指数退避：429、5xx、超时和空正文会重试；401/403/404/400 等不可恢复错误会快速终止并留下安全错误摘要。
- PDF 解析默认使用本地 **PyMuPDF**。选择 **MinerU** 时才显示其 Token、模型与测试设置；MinerU 不可用时会降级到 PyMuPDF。
- 参考文献 PDF 关键词提取可单独关闭；关闭后已提取关键词不显示、也不参与评分。关键词较多时使用固定高度原生滚动区域。
- 每日 0 点的独立关键词维护任务负责批量标准化和可选趋势报告；它失败不会阻断日报，下一次会重新尝试。

### 趋势研究

趋势研究是独立于日报的批量研究模式：指定关键词、日期范围和可选分类后，系统搜集论文、逐篇生成 TLDR，再让 SMART_LLM 对整体主题、时间演变、研究者、空白与方法趋势做综合分析。支持自定义分析提示词、HTML/Markdown 输出和独立成功/失败通知。

## 📜 历史导入、补充报告与过去日报

### v3.2 旧历史导入是一次完整工作流

入口位于「数据管理 → 数据库备份」下方的“读取旧历史”。它只读取旧系统的 JSON 历史和 HTML 日报，之后所有运行都使用 SQLite。

1. **空闲等待与互斥**：任务进入 trigger 队列；每日研究、趋势分析、关键词维护或其他相关任务未空闲时，导入等待而不并发写库。
2. **解析与合并**：读取 v3.2 JSON 与全部 HTML 卡片，恢复元数据、评分、译文和深度分析。重复分析以时间最新的报告为准；现有 v4 的完整记录不会被较旧数据降级。
3. **缺失登记与重试**：缺报告卡、缺译文、缺深度分析或无法获取元数据的项目进入补充积压，不会假装完成；下次读取旧历史会再次尝试。
4. **时间段回扫**：按旧历史涉及日期分块扫描 arXiv，与 SQLite 对照找漏掉的论文，追加到同一补充积压。
5. **自动补充报告**：导入后自动衔接补充运行，按“本次最多处理论文数”分批走相同评分/翻译/分析/报告流程。成功项出账，剩余项持久保留。

导入、补充、过去日报都是大型任务：各平台通知会发送一个汇总结果；若任务整体完成但某一步有缺失、延后或失败，通知会包含简短的具体问题，而不是整段日志。

### 过去日报队列

在「每日推送 → 过去日报」选择一个过去日期范围并点击“开始运行”。范围内每一天都会写入 `backfill_queue`，由 worker 从早到晚顺序处理：

- 每天只抓取该目标日期的新论文，随后执行完整评分、翻译、可选 PDF 分析和报告生成。
- 某天因“本次最多处理论文数”未处理完时，同一天自动续跑；单天失败会保留错误并继续后续日期，便于后续重试。
- 运行中断或容器重启后，未完成日期恢复为待处理，不会把队列误标为成功。

## 🔔 通知、备份与可观测性

### 多平台通知

支持邮件、企业微信、钉钉、Telegram、Slack 与通用 Webhook。通知模板存放于 `configs/templates/`；只在总开关、渠道开关和对应凭据都配置完成时发送。

以下大型任务拥有结果通知：日常研究、趋势研究、旧历史导入（含自动补充）、手动补充报告、过去日报范围队列，以及发现新 GitHub Release 的更新提醒。通知写入 SQLite outbox，渠道暂时不可用时会留待后续补发；失败消息包含发生问题的阶段/摘要，敏感凭据与完整堆栈不会发送出去。

自动更新功能**只检查并通知** GitHub Release，不会拉取代码、覆盖容器或自行重启服务。

### SQLite 备份与 WebDAV

| 项目 | 行为 |
| :-- | :-- |
| 本地备份 | 每次每日运行结束创建 SQLite 一致性 gzip 快照。当天保留全部副本；昨天及更早的每一天只保留最新一份。 |
| 保留期 | WebUI 可设任意非负整数，默认 7 天；`0` 表示不按天数过期。 |
| WebDAV | 增量上传：数据库内容改变才上传；远端副本从不由本项目删除。配置、SQLite、关键词、报告可分别选择同步。 |
| 恢复 | 数据管理页可导出 zip，或导入 zip / gz / db。导入会校验并归档旧数据库；先停止正在写库的任务。 |

### 运行安全与可观测性

- 运行锁、共享空闲闸门和 WebUI trigger watcher 防止相互冲突的任务同时写入 SQLite。
- 每个来源有扫描收据；水位线只有在完整交付后推进。运行诊断展示近期完成率、通过率、通知积压与最近一次扫描。
- 每次 LLM 最终调用的成功/失败可在“数据分析 → LLM 健康”查看，错误自动脱敏。不会为了健康检查额外产生模型请求。
- 运行日志区域固定为 800px 高，溢出内容在原生滚动容器中查看；长列表同样限制首屏高度。

## 🐳 部署、升级与命令行

### Compose 运行与维护

```bash
# 查看服务与健康状态
docker compose ps

# 追踪 worker / WebUI 日志
docker compose logs -f arxiv-daily-researcher
docker compose logs -f config-panel

# 代码更新后重建并强制使用最新本地镜像
git pull
docker compose build
docker compose up -d --force-recreate
docker compose ps
```

worker 使用 `network_mode: host`，因此在 Linux/NAS 上访问宿主机本地 LLM 时，`.env` 中通常可直接填写 `http://127.0.0.1:<port>/v1`。WebUI 通过共享的 `data/`、`logs/`、`configs/` 和 `.env` 与 worker 协作；不要把其中一个容器指向不同的数据目录。

当前仓库提供本地 Compose 构建。GHCR 托管镜像会在 v4.0 功能稳定并正式发布后再启用；在此之前，不应把 `latest` 当作已发布的远端镜像标签。

### 常用 CLI

```bash
# 默认每日研究
python main.py

# 趋势研究
python main.py --mode trend_research \
  --keywords "quantum error correction" \
  --date-from 2026-01-01 --date-to 2026-03-31 \
  --categories quant-ph

# 读取 v3.2 旧历史（会自行等待空闲）
python main.py --mode legacy_import

# 手动处理现有补充积压
python main.py --mode supplement_run

# 将过去日期范围写入持久队列并顺序补跑
python main.py --mode backfill_run \
  --date-from 2026-01-01 --date-to 2026-01-07
```

Docker 中可把上面的 `python main.py ...` 替换为：

```bash
docker compose exec arxiv-daily-researcher python main.py --mode daily_research
```

`daily_research.run_time` 在 `configs/config.json` 或 WebUI“每日推送”中设置。修改时间后，点击侧栏“重启主研究容器”或执行 `docker compose restart arxiv-daily-researcher`，让 worker 重装 cron。

## ❓ 复杂问题排查

<details>
<summary><b>NAS 移动网络、重配 DHCP 或启停 Tailscale 后，容器突然无法解析 <code>export.arxiv.org</code>，这是代码问题吗？</b></summary>

通常不是。`NameResolutionError` / `Temporary failure in name resolution` 表示宿主机、Docker 或 Tailscale 的 DNS 在网络变化后没有刷新；业务层重试不能修复“容器没有任何可用 DNS”。先分别检查宿主机与 worker：

```bash
getent hosts export.arxiv.org
docker exec arxiv-daily-researcher getent hosts export.arxiv.org
docker exec arxiv-daily-researcher cat /etc/resolv.conf
```

先修复 NAS 的上游 DNS，再执行：

```bash
docker compose up -d --force-recreate
```

如果 DHCP DNS 经常不稳定，可在本机新增（不要提交）`docker-compose.override.yml`，为两个服务设置适合所在地网络的 DNS。`100.100.100.100` 只适用于已启用 Tailscale MagicDNS 的环境；它不是通用公共 DNS。
</details>

<details>
<summary><b>LLM 显示“未返回可用正文”、任务部分完成或补充队列持续存在，应该如何判断？</b></summary>

先看“数据分析 → LLM 健康”：它展示真实调用的最近终态和脱敏错误。如果是 401/403/404/400，核对模型名、Base URL、API Key 与网关兼容性；如果是 429、5xx、超时或空正文，系统已按全局重试策略尝试，并将未完成论文留在 SQLite 待重试。不要手工把论文标记成完成，也不要直接删数据库行。修复供应商或网络后再运行日报/补充流程即可复用已成功的阶段。
</details>

<details>
<summary><b>点击“读取旧历史”后好像没有立即开始，是不是按钮失效？</b></summary>

不一定。导入是独占工作流：当每日研究、趋势研究、关键词维护或相关任务持有活动闸门时，WebUI 只会写入 trigger 队列，worker 空闲后才认领。到“每日推送 → 状态面板 / 运行日志”查看 trigger 状态和 `legacy_import_*.log`。不要同时从 CLI 再启动第二个导入；它们会等待同一空闲闸门，重复点击不会加速。
</details>

<details>
<summary><b>旧历史导入如何保证重复覆盖、缺失重试和补充报告正确？</b></summary>

导入按稳定论文身份合并，重复分析按最新报告时间取值；较旧的 v3.2 数据不会降级完整的 v4 行。缺卡、缺译文、缺分析或回扫发现的漏论文进入 `supplement_backlog`，不会被标记为已交付。导入结束后自动触发补充流程，每批受“本次最多处理论文数”限制；无论暂时失败还是因上限延后，积压都会保留，下一次导入/补充可继续。可在 SQLite 备份后检查队列，不建议手改表。
</details>

<details>
<summary><b>过去日报范围很大，容器重启或某一天失败后，剩余日期会丢吗？</b></summary>

不会。每一天都是 `backfill_queue` 的持久行，按目标日期顺序认领；中断会把当前日期退回 pending，单日处理上限导致的剩余论文会自动续跑同一天。某天失败会记录错误并继续后续日期，结果通知会汇总失败日期与首个错误。再次从面板或 CLI 启动后，worker 会恢复可运行的条目。
</details>

<details>
<summary><b>SQLite 备份很多，WebDAV 又有不同副本，恢复前应怎样操作？</b></summary>

当天的本地快照会全部保留，这是防止一次运行中损坏后覆盖唯一恢复点；昨天及更早每天只留最新一个，再受保留期控制。先停止所有写库任务，使用“数据管理 → 生成导出”做一份当前 zip，然后导入目标 zip/gz/db。WebDAV 是增量归档且远端永不删除，所以恢复后请确认 `daily_research.db`、报告目录和配置范围是否需要一起恢复；不要用不明来源的 `.db` 直接覆盖运行中数据库。
</details>

<details>
<summary><b>更新提醒说有新版本，为什么容器没有自己升级？</b></summary>

这是设计如此。自动更新只比较 GitHub Release 并发送通知，不会在无人确认时拉取代码、重建镜像或重启正在研究的容器。阅读 release note、备份 SQLite 后，用上面的 Compose 更新命令完成受控升级；如使用未来 GHCR 镜像，也应固定已验证版本标签而非盲目追随 `latest`。
</details>

<details>
<summary><b>“核心相关性 V2”几乎没有论文通过，或者参考词/专家作者让无关论文排在前面，如何理解？</b></summary>

V2 的资格由主关键词的加权内容相关度和至少一个强匹配共同决定；参考关键词与专家作者不能让无关论文获得资格。它们只可用于已合格论文的排序。请确认“关键词”页已配置真正的主关键词，再在“评分”页调整核心相关度阈值和强匹配阈值；若你的研究依赖大量参考文献术语且主关键词尚未整理，可暂时使用兼容的加权关键词 V1。
</details>

## 📁 项目结构

```text
arxiv-daily-researcher/
├── main.py                       # CLI 入口与模式分发
├── docker-compose.yml            # worker + config-panel
├── docker/Dockerfile             # 多阶段 worker / webui 镜像
├── configs/config.json           # JSONC 主配置与调度设置
├── configs/templates/            # 报告、邮件和通知模板
├── src/
│   ├── modes/                    # daily / trend / legacy / backfill 工作流
│   ├── agents/                   # 评分、关键词、趋势 LLM Agent
│   ├── sources/                  # arXiv、OpenAlex、HF Papers 等
│   ├── report/                   # 每日、趋势、关键词趋势报告
│   ├── notifications/            # 多渠道通知与 outbox
│   ├── keyword_tracker/          # 关键词标准化与趋势
│   ├── utils/                    # SQLite、队列、锁、备份、同步、健康检查
│   └── webui/                    # Streamlit 面板与 i18n
├── data/                         # SQLite、报告、队列、备份（运行时生成）
├── logs/                         # 系统与每次任务日志（运行时生成）
├── assets/                       # README 截图
└── tests/                        # 回归与工作流测试
```

## 🧪 验证、贡献与许可证

```bash
venv/bin/pytest -q
docker compose build
docker compose up -d --force-recreate
docker compose ps
```

欢迎通过 [Issues](https://github.com/yzr278892/arxiv-daily-researcher/issues) 提交真实复现步骤、日志中已脱敏的错误摘要或功能建议。修改后请保留 AGPL-3.0 的同许可证要求；详见 [LICENSE](LICENSE)。

完整版本历史请查看 [CHANGELOG.md](CHANGELOG.md)。

<div align="center">

如果这个项目帮助了你的研究，欢迎点一个 Star ⭐

[![Star History Chart](https://api.star-history.com/svg?repos=yzr278892/arxiv-daily-researcher&type=Date)](https://star-history.com/#/yzr278892/arxiv-daily-researcher&Date)

</div>
