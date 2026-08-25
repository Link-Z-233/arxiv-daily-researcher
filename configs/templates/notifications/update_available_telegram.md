# 更新通知模板 — Telegram HTML
#
# Telegram 使用 HTML 标签：<b> <code> <a> <blockquote>

<b>ArXiv Daily Researcher</b>
<b>🔄 新版本可用</b>

当前版本: <code>{local_version}</code>
最新版本: <code>{remote_version}</code>

<b>需要手动更新</b>
<blockquote>源码构建的 Docker: git pull && docker compose build && docker compose up -d
已使用托管镜像的 Docker: docker compose pull && docker compose up -d
本地 Python: git pull 后按你的方式重启服务</blockquote>

<blockquote>此提醒只负责检测，不会自行拉取代码、重建镜像或重启容器。</blockquote>

{release_notes}

<a href="{release_url}">查看发布页面</a>
