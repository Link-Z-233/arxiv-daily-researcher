# 多平台推广计划

## 执行优先级排序

| 优先级 | 平台 | 难度 | 预计流量 | 时间投入 | 状态 |
|--------|------|------|----------|----------|------|
| 🔴 P0 | Ruan Yifeng 周刊 | 低 | 高 | 10 min | 文案已备好 |
| 🔴 P0 | V2EX 分享创造 | 低 | 中高 | 15 min | 文案已备好 |
| 🔴 P0 | GitHub README 优化 | 中 | 持续 | 30 min | 待执行 |
| 🟡 P1 | 知乎文章 | 中 | 中高 | 1-2 h | 大纲已备好 |
| 🟡 P1 | Reddit r/MachineLearning | 低 | 中 | 15 min | 需英文文案 |
| 🟡 P1 | Hacker News Show HN | 低 | 中高 | 10 min | 需英文文案 |
| 🟢 P2 | 少数派 / sspai | 中 | 中 | 1 h | 需改写 |
| 🟢 P2 | Twitter/X | 低 | 低 | 5 min | 需英文 |
| 🟢 P2 | Awesome 列表提交 | 低 | 持续 | 30 min | 需 PR |
| 🟢 P2 | 即刻 / 小红书 | 低 | 低 | 15 min | 需中文短文案 |

---

## 各平台详细执行清单

### 🔴 P0-1: Ruan Yifeng 周刊
- **仓库**：github.com/ruanyf/weekly
- **操作**：提交 Issue，文案见 `ruanyifeng-weekly-submission.md`
- **时机**：立即（项目刚发 v3.2）
- **注意**：避免和上一次相同的表述，强调"重大更新"

### 🔴 P0-2: V2EX 分享创造
- **板块**：v2ex.com/go/create
- **操作**：发帖，文案见 `v2ex-post.md`
- **时机**：周二/三/四上午 10-11 点
- **注意**：准备截图，发帖后互动回复

### 🔴 P0-3: GitHub README 优化
- **具体操作**：
  - [ ] 在 README 开头增加英文简介段落（方便国际用户）
  - [ ] 添加 GitHub topics: `arxiv`, `paper-monitoring`, `llm`, `research-tool`, `academic`, `python`
  - [ ] 设置 repo description 更详细（当前需检查）
  - [ ] 添加 demo GIF/截图（尤其是 Web 面板）
  - [ ] 添加 "Quick Start" 一行命令
  - [ ] 在 README 中添加比较表格（与其他工具的差异）

### 🟡 P1-1: 知乎
- **内容**：基于 `zhihu-article.md` 大纲扩展
- **额外操作**：
  - 在相关问答下回答并引向文章
  - 话题：人工智能、科研工具、论文阅读、Python
  - 可拆成多篇：一篇讲思路、一篇讲技术实现

### 🟡 P1-2: Reddit r/MachineLearning
- **标题**："[P] ArXiv Daily Researcher - Open-source LLM-powered paper monitoring with deep PDF analysis"
- **要点**：强调 ML 社区关心的功能（成本追踪、LLM 评分、trend analysis）
- **规则**：注意 r/ML 的 self-post 规则，必须是 text post 而非纯链接

### 🟡 P1-3: Hacker News Show HN
- **标题**："Show HN: ArXiv Daily Researcher - LLM-powered academic paper tracking and analysis"
- **时机**：工作日下午（EST 时间）
- **注意**：Show HN 要求可以试用，确保 Docker 部署文档没问题

### 🟢 P2-1: 少数派
- **角度**：效率工具 / 科研工作流优化
- **风格**：更偏用户体验和使用场景，少谈技术细节
- **要求**：少数派对内容质量要求较高，需要认真写

### 🟢 P2-2: Awesome 列表
可以提交 PR 到以下列表：
- `awesome-arxiv` 
- `awesome-python`
- `awesome-research-tools`
- `awesome-llm-apps`
- `awesome-scholarly-data`

### 🟢 P2-3: Twitter/X + 即刻 + 小红书
- **Twitter**: 英文，简短介绍 + 截图 + GitHub 链接，打上 #arxiv #llm #opensource 标签
- **即刻**: 中文，轻松语气，配截图，发在"AI 探索站"圈子
- **小红书**: 科研效率工具合集风格，带个人使用体验

---

## 时间线建议

```
Day 1（今天）
├── ✅ 文案准备完成
├── 优化 README（增加 English 简介 + demo 截图）
└── 设置 GitHub topics 和 description

Day 2-3
├── 发布 V2EX 帖子
└── 提交 Ruan Yifeng 周刊 Issue

Day 4-5（根据反馈）
├── 如果 V2EX/周刊有流量 → 趁热发知乎文章
├── 如果 Reddit/HN 合适 → 英文版发布
└── 如果国内反馈好 → 同步少数派投稿

Day 7+
├── PR 提交到 Awesome 列表
└── 社交媒体持续分享
```

---

## 核心信息一致性

所有平台统一传达的核心信息：
1. **定位**：基于 LLM 的全自动学术论文监控与深度分析系统
2. **核心差异**：双 LLM 评分 + 趋势研究 + Docker 零门槛
3. **目标用户**：需要持续追踪论文的科研工作者/研究生
4. **一句话**：一行命令，自动从 20+ 期刊抓论文、AI 评分、深度分析、推送报告
