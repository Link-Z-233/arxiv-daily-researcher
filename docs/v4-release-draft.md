# v4 Release Draft

> 临时发布清单。它只记录已经明确决定纳入 v4 的内容；不会创建 tag、修改正式版本号或 push。

## 发布状态

- 目标版本：`v4`（具体小版本号和发布日期待定）
- GitHub 远端基线：`v3.2` / `a42a522`
- 当前本地开发分支：`main`，领先远端且尚未 push
- `VERSION`、README、CHANGELOG、正式 release notes：尚未更新
- 用户反馈/历史人工评分：明确不纳入 v4
- 旧 JSON 日报历史：不迁移、不兼容、不双写；SQLite 是唯一日报历史系统

## 已确认纳入 v4

### 1. Docker 镜像简化与安全修复

- Worker 与 WebUI 拆分依赖：`requirements-core.txt`、`requirements-webui.txt`。
- Worker 不再安装 Streamlit 及其 pandas/pyarrow/numpy/pydeck 依赖链。
- WebUI 不再安装 Worker 的 PDF、LLM 与论文抓取依赖链。
- Worker 镜像移除运行期不需要的 `git`。
- 保留现有 cron、trigger watcher、`tail -f` 长运行生命周期，不改为 run-once。
- 配置面板默认只绑定宿主机 `127.0.0.1:8501`。
- cron 不再把容器的全部环境变量及密钥复制到 `/etc/environment`；应用密钥仍由挂载的 `/app/.env` 读取，WebUI 仍可编辑、保存和导出 `.env`。
- 最终构建后记录 Worker/WebUI 镜像大小并做不触发日报的 smoke test。

### 2. SQLite 日报历史重构与可靠恢复

- SQLite 完全取代 `data/history/*_history.json` 作为每日研究历史；日报既不读取，也不写入旧 JSON 历史。
- 每篇论文持久化元数据、首次/最近发现时间、精确身份、阶段状态、阶段结果、输入指纹、重试次数、最近错误和完成时间。
- 同一 `(source, canonical_id, version)` 只能进入日报一次。
- arXiv `v1`、`v2`、`v3` 是独立版本：每个新版本重新评分、翻译、按配置分析并可重新推送。
- 新版本报告显示醒目的版本标识、上一已推送版本及其推送时间。
- 评分、摘要翻译或必需的深度分析失败时不交付、不记为完成；保留成功阶段并在下一次运行优先重试。
- TLDR、arXiv PDF URL 等可选增强在重试时不会被暂时性上游失败覆盖丢失。
- 报告文件存在且非空后，论文交付、运行完成、通知 outbox 和后续维护任务在一个 SQLite 事务中提交。通知/WebDAV 失败不会使论文第二天重新变成“新论文”。

### 3. 完整抓取、可观测扫描与处理数量上限

- arXiv 对每个领域同时扫描时间窗口内的首次提交和最后更新，完整分页，不使用 LLM 预算或论文数量作为抓取截断条件。
- 每个来源必须写入终态扫描收据；任一领域、分页或来源失败会使本次运行明确失败，不会把部分结果伪装成完整日报。
- 成功扫描水位线只在完整运行提交时推进；失败间隔会扩展下次恢复窗口，精确版本交付账本负责消除重叠扫描产生的重复项。
- `daily_research.max_papers_per_run = 0` 默认处理全部待处理论文。
- 正数只限制本次评分/翻译/分析数量，用于测试或主动限量；所有抓取候选先原子写入 SQLite，超出部分形成持久队列，下一次优先处理，不会因扫描水位线推进而丢失。
- 失败/重试论文优先于普通积压论文。

### 4. 数据源与报告页重构

- WebUI 核心来源只显示 arXiv、PRL。
- 其余来源统一进入默认关闭的“额外来源”；关闭时保留定义但不抓取。
- 额外来源使用声明式 JSON，当前只允许 `openalex_journal`（唯一 code、名称、ISSN）和 `huggingface_papers` 内置补充流。
- 严格拒绝 import path、Python、callback 等可执行字段；配置不会执行用户粘贴代码。
- 原 PRA/PRB/Nature/Science 等来源可复制内置声明直接启用，无需修改代码。
- 报告查看页自动发现 `data/reports/daily_research/html/<source>/` 下任意来源，“显示非 arXiv 来源”开启后统一展示，不再为每个来源单独修改 UI。

### 5. WebUI 交互收尾

- 扫描覆盖收据和运行健康摘要放入默认折叠的“高级诊断”。
- 网络代理默认关闭；关闭时折叠代理地址、no-proxy 和服务范围，同时保留原配置值。
- “每日研究持久化”明确显示 SQLite 为必需历史基础设施，不再提供关闭开关。
- 每日推送面板提供 `0 = 全部` 的本次处理数量上限。

## 发布前验收

- [x] 全部单元测试、编译检查和 `git diff --check` 通过（221 passed + 58 subtests）。
- [x] 每项新增功能保持独立提交，不提交用户自己的 README/promo 改动（4 个功能提交：source registry / SQLite queue / config IO / WebUI polish）。
- [x] Worker 与 WebUI 最终镜像均可构建（Worker ≈ 384MB，WebUI ≈ 785MB）。
- [x] Worker 仅启动 cron/watcher，使用不会命中的测试 cron，`RUN_ON_STARTUP=false`、`SETUP_WIZARD=false`，确认没有执行每日研究。
- [x] WebUI 健康检查通过（`/_stcore/health` 返回 200；宿主机 8501 被无关项目占用，smoke test 使用临时端口映射验证，compose 仍绑定 `127.0.0.1:8501`）。
- [x] 只删除属于本项目的旧容器和旧镜像；不删除卷，不执行全局 `image prune`。
- [ ] 重建并启动最终本地测试部署，不 push。

## 决策记录

| 日期 | 决策 |
| --- | --- |
| 2026-08-21 | 开始准备 v4 临时发布清单。 |
| 2026-08-21 | 纳入 Docker 瘦身和 Docker 安全修复，保留现有长运行生命周期。 |
| 2026-08-21 | SQLite 完全替代旧日报 JSON 历史，不迁移、不双写。 |
| 2026-08-21 | 精确版本独立交付，新版本重新评分并显示上一版本推送记录。 |
| 2026-08-21 | 默认处理全部；正数上限只限下游处理，完整抓取结果先进入 SQLite 队列。 |
| 2026-08-21 | 核心来源为 arXiv/PRL，其他来源改为安全声明式扩展和通用报告发现。 |
| 2026-08-21 | 历史人工评分及用户反馈暂不纳入 v4。 |
