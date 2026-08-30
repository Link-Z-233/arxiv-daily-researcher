# 项目交接报告

本文用于将 ArXiv Daily Researcher 从一台开发或部署主机完整迁移到另一台主机。代码、分支与发布信息保存在 GitHub；含凭据和研究状态的运行文件通过单独的加密归档迁移。

## 当前基线

- 发行版本：v4.2
- 默认分支：`main`
- 管理面板：现代 ASGI WebUI，默认监听 `127.0.0.1:8501`
- 容器：`arxiv-daily-researcher`（worker）与 `config-panel`（WebUI）
- 运行配置：`runtime/config.json`，Git 忽略
- 示例配置：`configs/config.example.json`，Git 跟踪

除 `main` 外，还保留以下分支供追溯和继续开发：

- `archive/streamlit-webui`：Streamlit 面板归档
- `feature/modern-webui`：现代 WebUI 的历史开发分支
- `copilot-worktree-2026-03-09T12-38-51`：未合并的历史工作分支

## 已迁移到 GitHub 的内容

以下内容应通过 GitHub 克隆或拉取恢复：

- 完整源码、测试、Docker Compose、工作流、文档和版本历史
- v4.2 的数据库恢复互斥保护
- `runtime/` 运行配置目录迁移、旧配置兼容复制与 WebDAV 路径兼容
- 所有本地分支和标签

以下内容不能提交到 GitHub，因为其中可能包含 API Key、Webhook、账户信息或私人研究数据：

- `.env`
- `runtime/config.json`
- `data/`：SQLite、报告、队列、备份、PDF 与历史数据
- `logs/`
- `promo/` 与本机 IDE 设置

这些文件应从本次生成的加密交接归档恢复。

## 新主机恢复步骤

1. 克隆仓库并切换到 `main`：

   ```bash
   git clone https://github.com/yzr278892/arxiv-daily-researcher.git
   cd arxiv-daily-researcher
   git switch main
   git pull --ff-only
   ```

2. 从交接归档所在的安全存储下载 `.tar.gz.gpg` 文件与同目录的 `.sha256` 清单，先验证校验和：

   ```bash
   sha256sum -c arxiv-daily-researcher-handover-*.sha256
   ```

3. 使用交接时单独保存的密码解密并在仓库根目录恢复：

   ```bash
   gpg --decrypt arxiv-daily-researcher-handover-*.tar.gz.gpg | tar -xzvf -
   ```

   完整归档会恢复 `.env`、`runtime/`、`data/`、`logs/`、`promo/` 和本机 `.vscode/` 设置；还会保留在 `handover_worktrees/arxiv-daily-researcher-modern/` 下的历史现代 WebUI worktree 数据与日志。归档不会覆盖 Git 代码。

4. 按新主机实际用户调整 `.env` 中的 `PUID` 与 `PGID`，然后构建并启动：

   ```bash
   docker compose up -d --build
   docker compose ps
   ```

5. 打开 `http://127.0.0.1:8501`，确认登录、运行诊断、报告查看、SQLite 备份和 WebDAV 连接正常。

## 恢复后的核验

```bash
docker compose ps
docker compose logs --tail=100 arxiv-daily-researcher
docker compose logs --tail=100 config-panel
docker compose exec arxiv-daily-researcher python /app/src/utils/container_health.py worker
docker compose exec config-panel python /app/src/utils/container_health.py webui --url http://127.0.0.1:8501/api/health
```

恢复后建议先将“本次最多处理论文数”设为 5，运行一次受控每日研究或过去日报，再恢复日常上限。

## 安全与清理原则

- 加密归档的密码不写入仓库、README、Issue 或提交信息；请存入密码管理器。
- 验证 GitHub 提交、远端分支、归档 SHA-256 和解密列表后，才删除旧主机文件。
- Docker 容器与本地镜像可在归档和推送验证后移除；它们均可由新主机重新构建。
- `venv/`、Python 缓存和 Docker 构建缓存无需迁移，按依赖文件重建即可。
