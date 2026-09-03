# 开源搜索发现层接入说明

## 目标

在不购买商业搜索 API、不使用代理账号池、不绕过登录与验证码的前提下，持续发现小游戏相关的公开网页、平台搜索页和经审核的 Feed。

## 已接入组件

| 组件 | 用途 | 费用 | 许可证/注意事项 |
|---|---|---:|---|
| SearXNG | 跨搜索引擎聚合；对抖音、小红书、B站、TapTap、公众号执行 `site:` 限定搜索 | 无按次 API 费 | AGPL-3.0；建议作为独立服务运行，若修改并通过网络提供服务，应履行对应许可证义务 |
| RSSHub | B站视频搜索、TapTap评价等路由输出 RSS/Atom | 无按次 API 费 | AGPL-3.0；平台路由可能变化或失效，必须显示健康状态 |
| changedetection.io | 监控指定游戏页、公告页、已知文章链接的页面变化 | 无按次 API 费 | 可选 Docker profile，适合“已知 URL”而非全网搜索 |
| 新闻 RSS | 公开新闻与行业媒体发现 | 无按次 API 费 | 搜索结果可能不完整，需保留来源链接 |

## 为什么没有接入 MediaCrawler

MediaCrawler 的当前许可明确限制为学习和研究用途，未经书面同意不得商业使用，也不得用于大规模抓取。这个项目面向产品化，因此不把它放进主链路。

## 运行方式

```bash
./scripts/bootstrap.sh
docker compose up -d
```

打开：`http://localhost:8787`

验证：

```bash
curl http://localhost:8787/healthz
curl 'http://localhost:8787/api/search?q=梦回甄嬛传&platform=bilibili&time_range=month'
```

## 定时归档

GitHub Actions 每 6 小时临时启动 SearXNG 与 RSSHub，执行配置文件中的查询，归档到 `data/live.json`。归档必须满足：

```json
{
  "schemaVersion": 2,
  "mock": false
}
```

## Vercel部署

Vercel可以托管 `index.html` 与 `/api/*` 代理函数，但不能在同一项目里常驻运行 SearXNG/RSSHub Docker 容器。即时搜索需要在一台可长期运行 Docker 的服务器上启动本仓库，然后在 Vercel 设置：

```text
SEARXNG_URL=https://你的搜索服务域名
LIVE_DATA_URL=https://raw.githubusercontent.com/kyirexy/deepfeed/main/data/live.json
```

若不配置 `SEARXNG_URL`，前端仍能展示 GitHub Actions 定时生成的真实归档，但“即时全网搜索”会明确返回未配置，不使用 mock 结果。

## 数据语义

- **搜索发现**：搜索引擎已索引的公开链接及摘要。
- **搜索 Feed**：平台搜索结果由审核路由转成 Feed。
- **评论 Feed**：指定页面或游戏的评论/评价 Feed；不等于平台全量评论。
- **新闻 RSS**：新闻搜索或媒体 RSS。

这四种数据不可混称为“全量评论”。
