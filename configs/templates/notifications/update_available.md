# 更新通知模板 — 通用 Markdown
#
# 可用变量（使用 {变量名} 引用）：
#   {local_version}    — 当前版本号
#   {remote_version}   — 最新版本号
#   {release_url}      — GitHub Release 页面链接
#   {release_notes}    — 更新日志摘要

## ArXiv Daily Researcher

**🔄 新版本可用**

> 当前版本: `{local_version}`
> 最新版本: `{remote_version}`

**需要手动更新**
> 源码构建的 Docker：`git pull && docker compose build && docker compose up -d`
> 已使用托管镜像的 Docker：`docker compose pull && docker compose up -d`
> 本地 Python：`git pull` 后按你的方式重启服务

> 此提醒只负责检测，不会自行拉取代码、重建镜像或重启容器。

{release_notes}

[查看发布页面]({release_url})
