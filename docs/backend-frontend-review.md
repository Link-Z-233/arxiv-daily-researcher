# 后端 ↔ 前端协作审查与轻量化方案

> 撰写日期：2026-08-22（第三批审查）· 基线：本地 main（领先远端 79 个提交）
> 范围：触发/监控链路、双容器协作、数据源定位、镜像体积。只审查不动代码。
> 结论速览：**发现 6 个协作层问题（2 个高优先）+ 1 个配置级问题 + 三层轻量化路线**。

---

## 1. 触发与监控链路审查（你说的"前端监控不好"的根因）

### 现状链路

```
WebUI「立即运行」→ data/run/webui_triggers/*.json（原子写）
   → worker entrypoint.sh trigger_watcher（每 5s FIFO 轮询）
   → spawn python main.py（写 .pid + status/*.json: running→succeeded/failed）
   → WebUI 只在"用户交互触发 rerun"时重新读锁文件/状态文件/日志尾部
```

### 问题清单（按优先级）

| # | 问题 | 证据 | 影响 |
|---|---|---|---|
| M1 | **无自动刷新**：运行状态、触发进度、日志尾部全部是静态快照，只在用户点按钮/切页面时更新；i18n 文案却声称"实时"（误导） | run_manager.py 全部 `st.rerun()` 都是动作后一次性；无 st_autorefresh/fragment；i18n.py:897-900 | **你的核心痛点**：点了"立即运行"后要反复手动刷新才知道进展 |
| M2 | **全局积压队列不可见**：1489 篇 pending 只有折叠"高级诊断"里一个窗口化阶段统计；`select_pending_papers` 的总量 UI 从未调用 | daily_research_store.py:1932-1937 无 webui 调用方 | 不知道队列还剩多少、按当前上限还要跑几天 |
| M3 | **无停止/取消**：run_manager.py 文档字符串承诺"停止进程"，i18n 留有孤儿键（stop_all_btn 等），实现已被移除 | grep 无任何 kill/terminate | 长运行（如全量消化 1489 篇）一旦启动就无法从 UI 中止 |
| M4 | **触发状态可误导**：cron 正在跑时触发请求只是排队；run_lock 冲突时 main.py 静默 `sys.exit(0)`，触发状态记为 `succeeded` 但实际没跑 | run_lock.py:172-188；watcher 不检查 worker 是否忙 | UI 显示"成功"但报告没有更新，用户困惑 |
| M5 | **trend_runner 无防重复**：运行按钮无 disabled 守卫、无 pending 态处理 | trend_runner.py:109-115 | 重复点击靠 worker 锁静默吞掉 |
| M6 | **worker 镜像包含 src/webui/ 全树**（从不运行）；webui 镜像里的 safe_url.py 无任何引用；根目录 requirements.txt 已无人引用 | docker/Dockerfile:24；Dockerfile.webui:20 | 无功能影响，但污染镜像与构建上下文 |

### 改进方案（v4.0 第三批建议，均为小改动）

1. **局部自动刷新（修 M1，不加依赖）**：Streamlit 1.60 已支持 `@st.fragment(run_every="5s")`。把"当前运行状态 + 最新日志尾部"包成 fragment，仅在锁被持有或触发 pending 时以 5s 频率自动重跑，空闲时静态（零开销）。日志查看器加"自动滚动"开关。
2. **队列深度上移（修 M2）**：每日推送页顶部常驻三格指标：`待处理队列 N 篇`、`失败待重试 N 篇`、`按当前上限预计 X 次运行`。数据源就是已有的 `select_pending_papers`。
3. **停止按钮（修 M3）**：向 trigger_watcher 的子进程发 SIGTERM（main.py 已把 SIGTERM 映射为 KeyboardInterrupt→状态落库为 interrupted，天然安全）。UI 只在锁被持有时显示"⏹ 停止本次运行"，二次确认后写一个 `stop_request` 文件，由 watcher 转发信号。
4. **真实状态（修 M4）**：run_lock 跳过路径在状态文件里写 `state=skipped_busy`（区别于 succeeded）；UI 对 skipped_busy 显示"已有运行在进行，本次请求未执行"。
5. **trend_runner 补 can_run 守卫（修 M5）**：复用 run_manager 的 pending/lock 逻辑。
6. **镜像清理（修 M6）**：worker 移除 `COPY src/webui/`；webui 移除 safe_url.py；删除根目录 requirements.txt；.dockerignore 补 docs/、promo/、tests/、assets/、README_EN.md、CHANGELOG.md。

### 审查中确认"没问题"的部分（不用改）

- **双容器 SQLite 并发**：`journal_mode=WAL` + `busy_timeout=30000`（daily_research_store.py:58-63），收藏写入与 worker 写入共存安全。
- **瘦镜像导入链**：全量审计通过——所有 webui→utils 导入都在 COPY 清单内；`from config import settings`、`from openai import ...`、`from sources...` 均有 try 守卫（此前修复的 store 迁移是唯一坑）。LLM"测试连接"按钮在 webui 镜像里会优雅报"openai 未安装"（可选：webui 加 openai 包，+80MB，不建议）。
- **触发请求安全**：≤32KB、原子写、worker 二次校验、无 shell、错误截断 4000 字符——设计良好。
- **watcher 崩溃安全**：启动时把孤儿 `.running` 重新入队（重启后不丢请求）；子进程失败不杀 watcher。

---

## 2. 数据源定位（arXiv 为主，其余辅助）

现状已经与你的定位一致，**不需要重构**：

- 运行配置 `enabled: ["arxiv"]`，只有 arXiv 在跑；PRL 是"核心可选"但默认未启用；PRA/PRB/Nature/Science/HF 全部在默认关闭的声明式 `extra_sources` 里（保留定义、不抓取、不耗资源）。
- 代码层面 OpenAlex/HF 源是独立模块 + 注册表声明，不掺入 arXiv 主路径；跨源去重只在多源启用时才生效。
- 建议：把 openalex/hf 维持"维护模式"（修 bug、不加功能）；WebUI 搜索页文案已是"核心来源 arXiv/PRL"。唯一可做的小优化——报告查看页把 arXiv 置顶排序（目前按来源目录名排序）。

**一个影响体验的配置问题（非代码 bug，已处理）**：`primary_keywords` 为空、只有 8 个 reference 关键词，V2 评分策略运行在降级模式（日志警告"未配置可用 PRIMARY_KEYWORDS，以全部关键词作为核心集合降级"），核心门槛 6.0 几乎不可能达到——这是及格率 0% 的直接原因。**2026-08-22 决策：已切换回 `legacy_weighted_keyword_v1`**（通过分 = 1.5 + 2.5 × 关键词总权重 = 11.0，配合 reference 关键词即可正常工作）。实测切换后评分恢复区分度（10.5 / 0.0 / 0.0）；若想让 10.5 分一档的论文过线，可在评分页微调 `base_score`/`weight_coefficient`。V2 代码保留可随时切回。

---

## 修复执行记录（2026-08-22 第三批，M1–M5 + L1/L2 全部完成并实测）

| 项 | 提交 | 实测验证 |
|---|---|---|
| M1 自动刷新（st.fragment 5s） | c2f8d62 + 92b4ea3 | ✅ 150 秒无交互，日志尾部自动从"阶段3"推进到"评分 2/3" |
| M2 队列指标上移 | 3b22f94 | ✅ 页面常驻"待处理队列 / 失败待重试 / 还需 497 次运行" |
| M3 停止运行 | fb2b227 + 92b4ea3 | ✅ 停止控件自动出现→确认→`interrupted rc 130` |
| M4 skipped_busy | 896887e | ✅ 单测覆盖（退出码 75 → 状态 skipped_busy） |
| M5 trend 守卫 | 921a34a | ✅ 编译 + 与每日推送同款 gating |
| L1+L2 镜像瘦身 | 0d985cc | ✅ worker 384→346MB，webui 785→692MB，统一多阶段共享基底 |
| 评分切回 v1 | （运行时配置，未入库） | ✅ 及格分公式 11.0 生效，评分恢复区分度 |

---

## 3. 轻量化方案（不减功能）

### 体积构成（实测 2026-08-22）

| 镜像 | 实测 | 构成 |
|---|---|---|
| worker | 384MB | python:3.12-slim ≈125MB + cron/tzdata ≈10MB + pip ≈250MB（PyMuPDF≈50、openai+pydantic≈30、httpx/requests/tenacity 等） |
| webui | 785MB | 基底 125MB + Streamlit 全家桶 ≈640MB（pandas≈60、numpy≈40、pyarrow≈40、altair/pydeck/tornado/watchdog…） |
| 合计磁盘 | 1169MB | 两镜像仅共享 slim 基底层 |

### 三层路线（按投入递增）

**L1 零风险清理（预计 worker −30~50MB、webui −40~60MB，1 天内完成）**
- 多阶段构建：builder 阶段 `pip install --prefix`，final 只拷 site-packages，不带 pip/setuptools/wheel、`.dist-info`、`__pycache__`、包内测试文件。
- M6 的四处清理（webui 树出 worker、safe_url 出 webui、删 requirements.txt、补 .dockerignore）。
- worker 镜像 `PYTHONDONTWRITEBYTECODE=1` 运行期本就生效，构建期再显式 `--no-compile`。

**L2 共享基底（总占用再 −50~80MB，改动小）**
- 两个 Dockerfile `FROM app-base`（slim + 公共依赖：requests/json5/webdavclient3/tzdata），worker/webui 各叠自己的层。单机总磁盘 = 共享层 + 两份独有层；构建缓存也共享。
- 顺带把 `webdavclient3` 从 worker 移回核对：worker 的 WebDAV 同步确实用它（保留）。

**L3 结构性方案（收益最大但投入大，二选一，建议远期）**
- **方案 A：单镜像合并部署**（不删功能）：一个镜像装全部依赖（≈850MB），supervisor 同时拉起 cron+watcher 和 streamlit。总磁盘 1169→850MB（−27%），部署从两容器变一容器。代价：WebUI 重启会带走正在运行的每日研究（可用 supervisor 停止顺序缓解，且 trigger 队列重启后会重新入队，实际风险低）。
- **方案 B：替换 Streamlit**（webui 640→100MB 级）：FastAPI + 静态前端（htmx/原生 HTML，热力图已是纯 HTML/CSS，天然可迁移）。需要重写约 1.3 万行面板代码，与"不减功能"约束冲突风险最高，仅当 L1+L2 后仍不满意再考虑。

**不建议**：alpine 基底（PyMuPDF/科学栈的 musl wheel 兼容性地雷）；删 PyMuPDF（深度分析与参考文献关键词提取依赖它，属于功能）。

### 建议执行顺序

1. v4.0 第三批：M1 自动刷新 + M2 队列深度 + M4 真实状态（监控体验质变，用户感知最强）。
2. 同批或下一批：M3 停止按钮、M5 trend 守卫、M6 清理（顺手）。
3. L1+L2 镜像瘦身（独立于功能，随时可做）。
4. 配置修正：补 3–5 个主要关键词（马上就能改善报告质量）。
5. L3 远期评估。
