# V2 稳定日报与推荐质量路线图

本文记录 V2 的工程边界、已完成的可靠性工作、fork 调研结论，以及后续功能的独立交付顺序。目标不是增加一个会偶尔推荐论文的演示功能，而是让项目能够长期、可审计地运行：在配置的范围内完整发现论文，可靠地完成一次交付，并且能以真实反馈持续改进推荐质量。

## 不可破坏的运行约束

1. 每日扫描必须处理配置时间窗内的**全部**候选论文；不得以 `max_results`、LLM 预算、Top-N 或“成本保护”把论文留到下一天。
2. 上游服务保护只通过分页、请求限速、指数退避和明确失败实现。任一必需数据源/领域不完整时，日报必须失败并在下次恢复扫描，不能把部分结果伪装成完整日报。
3. `(source, canonical_id, version)` 只有在报告已原子写入并提交交付账本后，才能算作已交付。评分、翻译、深度分析失败必须保留为可重试状态；通知和 WebDAV 失败则通过独立 outbox 重试，不能重新打开“新论文”资格。
4. arXiv 新版本是新的交付对象：必须重新评分、可重新深度分析、可重新推送，并在报告中显示旧版本与此前推送时间。
5. 所有持久化的评分证据、配置摘要和评测产物不得包含 API key、Webhook、SMTP 密码或其他密钥。

## 已完成的稳定性底座

| 主题 | 已实现的行为 | 验收方式 |
| --- | --- | --- |
| Docker | runtime 与 build 依赖分层、移除不需要的构建工具；entrypoint 不再把完整环境（含密钥）写入 `/etc/environment`；保持当前容器内 cron/watcher/tail 运行方式不变 | `cbe14af`、`fba47fc`，`bash -n docker/entrypoint.sh`；镜像构建与运行检查 |
| arXiv 完整性 | `SubmittedDate` 和 `LastUpdatedDate` 双查询、无数量截断分页、失败闭锁、恢复水位线重叠扫描 | 单元测试覆盖分页、修订版和失败恢复 |
| 抓取可观测性 | 每轮计划来源都持久化终态收据；arXiv 额外保留领域/查询级窗口、分页与去重证据，HF/OpenAlex 保留来源汇总。WebUI 安全展示最近运行的所有计划来源，明确标出缺失、失败或损坏收据 | 收据写入失败会让本轮失败并保留恢复窗口；成功/失败/缺失/旧数据库只读查看均有回归测试 |
| 延迟公告 | 正常窗口外默认额外回看 2 天，覆盖周末公告/API 索引延迟；精确版本账本去除重叠推送 | `a2a4fcb`，可在 WebUI 调整 0–30 天 |
| 版本与历史 | canonical ID + version 身份、SQLite 交付唯一约束、旧 JSON 历史兼容迁移、版本标签和历史推送记录 | 身份与交付账本测试 |
| LLM 阶段状态 | 评分、翻译、深度分析分阶段持久化，输出无效或空内容不再被误记为成功；输入变化会失效未交付缓存 | 阶段状态与 fingerprint 测试 |
| 报告/通知 | 报告原子写入，通知/WebDAV 使用独立 outbox，失败不会让论文次日再次成为新论文 | 报告与 outbox 测试 |
| 本地 WebUI | 配置写入原子化、触发请求参数化、报告预览隔离、动态 Markdown/HTML/URL 安全化；运行管理的收据/健康页均只读且不回显错误或请求文本；仍可直接导出配置 | WebUI 与安全测试 |
| 状态闭锁 | 运行只允许 `running -> completed/failed`；损坏分析缓存会被清除并重试；损坏水位线/扫描计划会终止本轮并保留恢复窗口 | `470cf93`、`b0a574d`；全量 195 项测试 |

当前设计仍有意保留的边界是：容器常驻调度模型没有在本轮改动。将来如迁移为 systemd timer/cron 触发的“运行一次即退出”任务，应作为单独的运维迁移设计，不能与可靠性状态迁移混在一起。

## 评分问题的诊断

当前模型对每个关键词给出 `0..MAX_SCORE_PER_KEYWORD`，总分为加权和，再以：

```text
passing_score = base_score + coefficient × Σ(keyword_weight)
```

判定及格。它有四个结构性问题：

1. **关键词数量改变了等效门槛。** 把两边同时除以总权重后，要求的平均相关度为 `coefficient + base_score / total_weight`。关键词/参考关键词越多，第二项越小；同一篇边缘论文可能仅因为增加了若干低权重关键词而更容易通过。
2. **资格与排序共用一个分数。** 多个弱相关信号相加可以掩盖“没有真正命中核心方向”的事实；反过来，一篇高度命中一个主方向的论文也会因其他不相关关键词被平均稀释。
3. **作者偏好可改变是否入选。** 即使作者名单是真实且确定性匹配，作者加分也只能表达“值得优先看”，不能让内容无关论文跨过相关性门槛。
4. **没有闭环标定。** 现在可见逐关键词分数与理由，但没有统一的人工正/负标注、阈值扫描、precision/recall 报告或模型/配置版本证据。因此直接调 `base`、`coefficient` 或 prompt 容易只是把假阳性换成假阴性。

近期已修复的输入校验（关键词集合、有限数值、范围、TLDR/理由非空、真实作者交集）解决了“错误输出被静默固化”的问题，但不会单独解决上述推荐校准问题。

## 推荐的 V2 评分模型

V2 将把四个概念分离，而不是继续把所有内容累加进 `total_score`：

| 概念 | 用途 | 不能做什么 |
| --- | --- | --- |
| 原始相关度 | 逐关键词分数、命中证据、主关键词加权平均 | 不直接代表排序偏好 |
| 资格门槛 | 判定是否推荐；要求达到归一化阈值，并满足至少一个核心主关键词/核心组命中 | 不受作者加分影响 |
| 排序分 | 在已合格论文中排序；可使用相关度、强匹配数量、版本新鲜度等 | 不能让未合格论文进入推荐区 |
| 作者偏好 | 已合格论文的透明排序加分或标签 | 不能单独使论文合格 |

建议的默认策略（在评测基线建立后单独上线）是：

1. 保留所有论文的全量评分与报告归档。
2. 对主关键词计算归一化加权相关度；参考关键词只作为辅助证据与排序信号，不能降低主门槛。
3. 至少一个配置的核心关键词达到明确的强匹配分数，或配置的核心关键词组达到规定的合取规则；这样可阻止“每个词都沾一点边”的论文通过。
4. 用无作者加分的 `relevance_score` 判定资格；作者命中只写入 `author_preference_bonus`，并在报告中分项展示。
5. 报告同时展示原始相关度、资格规则、排序分、匹配证据和策略版本；旧配置保留兼容模式，以便可逆迁移与 A/B 对比。

模型委员会不是默认方案。它可以作为将来**对每篇候选均执行、有限速和明确失败语义**的可选复核策略，但不能按预算截断，也不能用“模型失败时固定给 5 分”掩盖错误。单模型先配合真实标注调准，通常比未经校准的多模型投票更可信。

## Fork 调研结论

| Fork | 发现 | 决定 |
| --- | --- | --- |
| [CyanM2610](https://github.com/CyanM2610/arxiv-daily-researcher) | 延迟 arXiv 公告回看、事件契约和独立投递链路；其最新修复也按日期分片 | 吸收延迟回看为 `a2a4fcb`，但保留本项目无上限分页和“任一领域失败即失败”的闭锁语义；不引入其结果上限、部分窗口成功即日报成功或完整 AstrBot/QQ 部署栈 |
| [q1w2e3r4-1](https://github.com/q1w2e3r4-1/arxiv-daily-researcher) | 离线 ground truth、逐篇模型结果、precision/recall/F1、人工复查候选与评分审计产物 | 吸收评测与反馈方法；不直接移植其 MLSys 专用 prompt、多模型委员会、边界早停和失败 fallback 分数 |
| [smallflyingpig](https://github.com/smallflyingpig/arxiv-daily-researcher) | DBLP、PapersWithCode、Semantic Scholar、OpenReview、Hugging Face、Google Scholar 等来源 | 未来优先评估 OpenReview/Hugging Face；每个来源必须有完整分页、稳定身份、失败闭锁、交付去重和测试。拒绝 Google Scholar 无官方 API 的抓取方案，也不直接带入其结果上限/部分失败返回部分结果 |
| [singledog957](https://github.com/singledog957/arxiv-daily-researcher) | 深度分析开关、通知/WebUI/运行管理等演进 | 大部分能力已在当前主线存在；只持续借鉴 UI 可观测性，不重复搬运旧实现 |
| zhaoyb-coder、AppleOrBanana、Canjia-Huang | 主要是领域配置、定时表或旧结构调整 | 无值得直接吸收的通用可靠性改动 |

补充核查了几个较新的分支：

| Fork/分支 | 可复用的想法 | 处理决定与边界 |
| --- | --- | --- |
| [brilliantrough/arxiv-daily-researcher](https://github.com/brilliantrough/arxiv-daily-researcher/tree/feat/full-text-tldr) | 将全文 TL;DR 从评分阶段移到深度分析，并用 `content_source=pdf/abstract_fallback` 区分来源；报告渲染只显示有真实全文依据的 TL;DR | 已作为 P4.1 独立吸收：保留评分 TL;DR 兼容字段，深度分析来源由本地代码断言；摘要降级不会请求或展示全文 TL;DR |
| [PeriodBLUE/arxiv-daily-researcher](https://github.com/PeriodBLUE/arxiv-daily-researcher) | 将趋势模式的关键词查询从 AND 改为 OR | 不直接合并：当前日报扫描使用 `cat:<domain>` 的提交/更新双查询，未按关键词截断；OR 只适用于趋势检索，若未来开放趋势模式默认策略，须增加 AND/OR 配置和结果质量测试 |
| [fj5fj52010/arxiv-daily-researcher](https://github.com/fj5fj52010/arxiv-daily-researcher) | 兼容 `chat.completions` 内容数组、reasoning 字段和 Responses API 的文本提取 | 可吸收解析层与观测日志；拒绝其“LLM 失败返回 `{}`/空字符串后继续”的语义，生产失败必须保留为可重试错误 |
| [Akiq2016/arxiv-daily-researcher](https://github.com/Akiq2016/arxiv-daily-researcher)、[luckly06/arxiv-daily-researcher](https://github.com/luckly06/arxiv-daily-researcher/tree/dev) | Run Manager、数据库检索和运行状态 UI | 当前主线已有运行管理、扫描收据和本地触发队列；只挑选可观测性字段，不重复引入旧的状态/锁实现 |
| [smallflyingpig/arxiv-daily-researcher](https://github.com/smallflyingpig/arxiv-daily-researcher) | DBLP、PapersWithCode、OpenReview 等来源适配器 | 每个来源必须重新实现完整分页、稳定身份、失败闭锁和交付账本测试；HF 已单独吸收。Google Scholar 没有官方稳定 API，拒绝其抓取方案 |

## 分阶段交付顺序

### P0 — 可靠交付（已完成，持续回归）

- 完整抓取、恢复水位线、延迟公告回看。
- 每个 `prepare_scan` 中计划的数据源都必须写入一个终态扫描收据；arXiv 保留领域/查询级证据，Hugging Face Papers 与每个 OpenAlex 期刊写来源级成功/失败摘要。水位线和日报交付事务只在全部计划来源均有 `succeeded` 收据时推进；任何遗漏、失败或收据持久化错误都保持恢复窗口。
- 精确版本交付账本和修订版重推。
- 分阶段 LLM 状态、原子报告、通知/WebDAV outbox、WebUI 本地安全。
- 每次后续特性都必须运行完整测试，并且不能重新引入日报篇数预算。

### P1 — 评分评测与人工反馈基础设施（已实现，独立提交）

- 从 SQLite 评分账本导出不含密钥的 JSONL/CSV 标注候选，包含论文元数据、逐关键词分数、TLDR、理由、策略/model/config 证据。
- 导入人工标签（`relevant`、`not_relevant`、`unsure`，可带备注）；身份按 `(source, paper_id)` 精确匹配，冲突/坏数据 fail closed。
- 根据真实标签计算混淆矩阵、precision、recall、F1、pass rate，并对候选阈值做扫描；`unsure` 不参与二分类指标。
- 输出机器可读 JSON 和可读 Markdown 报告，列出 FP/FN 及边界样本，供人工检查。
- 只观测、导出和评测，不改生产判定逻辑；历史数据缺少证据时应明确标为 legacy，而不是假装完整。

**验收：** `0b13a39` 已实现上述导出、标签校验、阈值扫描与报告；同一数据库和同一标签集重复运行得到一致指标，损坏/重复标签不会静默被接受，导出/评测不读取或打印 `.env`。

### P1.1 — 评分漂移与运行健康诊断（已实现，独立提交）

- 新增只读 CLI：

  ```bash
  PYTHONPATH=src venv/bin/python -m utils.scoring_evaluation diagnose \
    --db data/daily_research/daily_research.db \
    --recent-runs 14 --baseline-runs 28 \
    --json-output data/diagnostics/daily-research.json \
    --markdown-output data/diagnostics/daily-research.md
  ```

- 按“最近运行窗口 / 更早基线窗口”比较资格率、评分分布（均值、中位数、四分位和固定分箱）、`core_relevance_v2` 的内容相关度与 legacy 总分，并显式显示样本不足而非补造结论。
- 汇总评分策略、policy fingerprint、模型名/温度的组合变化；不安全或损坏的标识只以短 fingerprint 表示。它只揭示策略变化，不读取或导出研究上下文、关键词原文、API 地址或凭据。
- 汇总运行状态、当前 `daily_papers` 的阶段状态、持久化重试计数、扫描收据、来源候选量、失败/缺失/损坏收据和 outbox 堆积；不输出论文内容、原始错误、arXiv 查询、报告路径、通知载荷、Webhook 或密码。
- 工具用 SQLite `mode=ro` + 单一只读快照工作，不创建/迁移数据库，不会触发评分、通知或重试。`daily_papers` 是可恢复的当前状态账本而不是逐次事件表，因此报告明确将重试数标为持久化总计；缺失扫描收据只意味着覆盖证据不足，不单独断言抓取失败。

**验收：** 有策略/模型和分数变化时可重现差异；失败扫描与重试查询进入聚合观察；诊断 JSON/Markdown 不包含刻意注入的私密 audit、错误或 outbox 字段；CLI 和 API 均由回归测试覆盖。

### P1.2 — 本地 WebUI 安全运行观测（已实现，独立提交）

- “运行管理 → 扫描覆盖收据”改为通过 SQLite `mode=ro` 的专用视图读取，不实例化会迁移/写入的 `DailyResearchStore`。旧数据库可被安全读取；若尚无收据表，只显示升级提示而不会创建表。
- 每个近期运行显示所有计划来源：已有收据、缺失收据、失败/损坏收据和来源级候选计数；arXiv 额外显示已白名单化的提交/更新查询计数、分页、窗口和去重计数。HF/OpenAlex 等来源使用相同的来源汇总行，不再被 ArXiv 专用页面遗漏。
- 该视图不显示原始收据、上游异常、查询文本、URL、报告路径、论文内容、通知载荷或凭据。WebUI 触发器的失败提示也只显示允许的终态和数值退出码，详细错误留在本地日志。
- 新增“运行健康摘要”，直接使用 P1.1 的只读聚合展示最近 10 次运行及 20 次基线中的评分通过率、来源证据异常、阶段状态和两个 outbox 的积压情况；它不改变任何调度、评分、交付或重试状态。

**验收：** 以含有刻意注入错误文本、URL 与查询文本的收据运行 UI/摘要测试，浏览器渲染和 API 输出均不含这些字段；缺失计划来源仍可见；只读打开旧数据库不会迁移或创建收据表。

### P2 — 新的默认资格/排序策略（已实现，独立提交）

- 已引入 `core_relevance_v2` 与 `legacy_weighted_keyword_v1`。旧配置未声明策略时继续使用 legacy；新生成的配置可显式选择 V2，并可在 WebUI 回退比较。
- V2 用主关键词的归一化内容相关度和“至少一个强主关键词命中”共同决定资格。参考 PDF 关键词仅给已合格论文的排序提供有限辅助；专家作者也只能给已合格论文增加排序偏好。
- `WeightedScoreResponse` 已持久化策略、核心相关度、资格阈值、强匹配证据、参考分和排序分；旧 SQLite `score_json` 仍按 legacy 兼容读取和渲染。
- 日报 Markdown/HTML、通知 Top-N、评分审计与缓存 fingerprint 都已携带策略证据；主关键词或 V2 阈值变化会使未交付缓存重新评分。

**验收：** 作者命中但核心方向为零不得合格；主方向强匹配不能因新增低权重参考词而降低资格；参考词和作者偏好能在合格集合内改变排序；旧评分记录、旧配置与 V2 报告/通知均有回归测试。

### P2.1 — LLM 响应兼容层（已实现，独立提交）

- 对 `chat.completions` 的字符串/内容数组、reasoning 字段和可选 Responses API 做统一的**结构化提取**。
- 空内容、JSON 解析失败、schema 校验失败必须进入评分/翻译/分析的失败状态并重试；不能返回固定低分、`{}` 或空字符串伪装成功。深度分析还会拒绝没有任何可渲染模板字段的元数据/错误 JSON，并在缓存读取时重新标记为可重试。
- 深度分析提示词为列表/inline 模块明确输出 JSON 数组；保留旧的可渲染字符串缓存兼容，并用新的分析 fingerprint 只重跑未交付的旧契约缓存。
- 继续沿用现有不含密钥的评分审计字段（模型、策略、fingerprint）；响应形态只参与内存中的统一解析，不持久化原始响应或任何凭据，并用 fixture 覆盖不同 SDK 响应形态。

**验收：** 每种支持的响应形态均能恢复正文；空/非法响应均能触发可观测重试；现有 195 项回归测试保持通过。

### P3 — 数据源扩展（每个来源一个独立提交）

每个来源都必须先完成：时间窗口/游标分页、源端延迟处理、规范化身份、精确交付去重、失败闭锁、可选富化与核心交付分离、fixture 测试和配置/WebUI 往返。不能用“某个第三方服务暂时不可用”把空列表当成功。

#### P3.1 Hugging Face Papers（已实现，独立提交）

- 新增默认关闭的 `huggingface_papers`。它是 Hugging Face 展示/精选的日榜，**不宣称全量 arXiv 覆盖，也绝不替代 arXiv 分类抓取**。
- 使用日期接口完整跟随可信 HTTPS `rel=next` 分页；空数组才是正常结束。任何非空页缺续页、循环/跳页、错误 host/date/page、非列表 JSON、坏条目或网络失败都会 fail closed。
- 默认 `availability_lag_days=2`，避免把尚未形成的当天榜单误当作空源；默认 `lookback_grace_days=2`，并以交付账本安全去重近期重扫。
- HF 使用独立 JSON 历史和 SQLite source identity；调度器已泛化为“报告 source → 后端 source”映射，不会再把非 arXiv 来源错误写入 OpenAlex history。
- 同轮 arXiv 记录优先于 HF 镜像；任何已交付 arXiv canonical ID 也会抑制其晚到 HF 镜像。此规则只抑制镜像，不会阻止 arXiv v2/v3 重新评分、分析和推送。
- HF 条目保留 arXiv PDF，能够进入现有深度分析；报告中明确标识其“补充精选流”属性。

#### P3.2 OpenReview（暂缓）

OpenReview API V2 支持 invitation、时间水位和分页，技术上适合“明确会议 invitation”的可选监控，但不适合声称全站论文源。当前匿名 `/notes` 实测会触发 `403 ChallengeRequiredError`，会降低无人值守日报稳定性；因此不会把一个默认无法稳定运行的来源接入主线。待 API 访问策略稳定后，再以会议白名单、全量分页与失败闭锁的独立提交评估。

#### P3.3 后续候选

再根据实际需求评估 DBLP/PapersWithCode。拒绝 Google Scholar 无官方 API 的抓取方案；不引入任何结果上限、部分失败返回部分结果或“空列表降级成功”的实现。

### P4 — 可选的深度复核与个性化（P4.1 已实现，其余后续设计）

- 在 P1 标签足够且 P2 已稳定后，才评估全量、限速的二次 LLM 复核。
- P4.1 已交付“全文 TL;DR 来源标识”：深度分析模板新增默认关闭的 `full_text_tldr`；本地代码为结果写入 `__meta.content_source=pdf/abstract_fallback`，PDF 解析失败时不会向模型请求该字段，Markdown/HTML 也只在 `pdf` 时渲染它。普通深度分析会醒目显示其 PDF 全文或摘要降级来源，内部元数据不进入报告；该功能不改变论文是否进入日报的资格规则。新 fingerprint 只使未交付的旧分析契约重跑。
- 可从人工反馈学习个人偏好，但必须保留可解释的硬相关性门槛，且不能把私有标注上传到外部服务。
- P1.1 已提供本地、只读的漂移报告：通过率、评分分布、模型/策略变更、来源收据缺口和失败重试状况。只有标注评测证明有改进时，才改变生产资格规则。

## 操作与审计建议

- 日常使用中将 `announcement_lookback_grace_days` 保持为 2；若目标分类周末/节假日延迟明显，可提高到 3–5。它只增加候选抓取量，不会重复推送已交付版本。
- 不要以手工删除 JSON 历史来“重新抓取”；SQLite 交付账本才是权威去重记录。需要重新评估时使用 P1 的导出/评测工具，而不是篡改历史。
- 更改关键词、模型、温度或分析模板后，未交付论文会由 fingerprint 自动失效并重试；已经交付的版本保持历史稳定，除非出现新的 arXiv 版本。
- 任何报告出现“抓取数突然为零”“某一分类异常少”时，应先检查该次运行是否明确失败、扫描水位线、arXiv 查询日志和延迟回看配置，而不是假定系统已完整扫描。
- 当前版本会在本地 WebUI 的“运行管理 → 扫描覆盖收据”中展示全部计划来源；先确认收据是否成功/缺失，再看 ArXiv 提交/更新查询的窗口内数量、历史跳过和去重。旁边的“运行健康摘要”可查看最近窗口的来源异常、阶段失败和 outbox 积压。两者均是只读审计辅助，不会替代 SQLite 交付账本或改变论文重试规则。
