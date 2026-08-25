# 更新通知模板 — 企业微信 Markdown
#
# 企业微信使用 <font color="..."> 标记颜色

## ArXiv Daily Researcher

<font color="warning">**🔄 新版本可用**</font>

> 当前版本: `{local_version}`
> 最新版本: `{remote_version}`

**需要手动更新**
> 源码构建的 Docker：`git pull && docker compose build && docker compose up -d`
> 已使用托管镜像的 Docker：`docker compose pull && docker compose up -d`
> 本地 Python：`git pull` 后按你的方式重启服务

> 此提醒只负责检测，不会自行拉取代码、重建镜像或重启容器。

{release_notes}

[查看发布页面]({release_url})
