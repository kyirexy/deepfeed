# 潮汐 · 小游戏公开舆情 AI 工作台（开源搜索版）

这是一套可以真实运行的公开舆情发现 Demo。它不使用 mock 数据兜底，也不依赖付费 Web Search API。

## 已接入

- **SearXNG**：搜索抖音、小红书、B站、TapTap、微信公众号、知乎、贴吧中被搜索引擎收录的公开页面。
- **RSSHub**：当前配置 B站视频搜索、TapTap最新评价与综合评价 Feed。
- **新闻 RSS**：持续发现新闻和行业媒体内容。
- **GitHub Actions**：每 6 小时执行一次真实采集，按 URL 去重并更新 `data/live.json`。
- **changedetection.io**：可选启用，用于已知游戏页、公告页和文章链接的变化监控。
- **明暗主题与现代化工作台 UI**：支持即时搜索、持续归档、来源健康和数据边界页。

## 一键本地运行

需要 Docker 与 Docker Compose：

```bash
./scripts/bootstrap.sh
docker compose up -d
```

然后访问：

```text
http://localhost:8787
```

检查服务：

```bash
curl http://localhost:8787/healthz
curl http://localhost:8787/api/health
curl 'http://localhost:8787/api/search?q=梦回甄嬛传&platform=taptap&time_range=month'
```

启用可选页面变化监控：

```bash
docker compose --profile monitoring up -d
```

## 无 Docker 的纯前端预览

直接打开 `index.html`。它会读取 GitHub 仓库中的真实 `data/live.json`；单文件模式下即时搜索需要在页面里填写一个已运行的 `/api/search` 地址。

## 配置监控对象

编辑：

```text
config/targets.json
```

可以配置：游戏名称、别名、包含词、排除词、目标平台、搜索后缀、新闻查询和 RSSHub 路由。

## 定时采集

工作流：

```text
.github/workflows/collect-public-feeds.yml
```

默认每 6 小时启动临时 SearXNG/RSSHub 容器并执行采集。公开仓库可利用 GitHub Actions 的公开仓库额度；运行结果会自动提交到 `data/live.json`。

## Vercel

Vercel可托管前端和 `api/*.js`。即时搜索仍需要一个长期运行的 SearXNG 地址，并在 Vercel环境变量中配置：

```text
SEARXNG_URL=https://search.example.com
LIVE_DATA_URL=https://raw.githubusercontent.com/kyirexy/deepfeed/main/data/live.json
```

SearXNG/RSSHub 是常驻容器服务，不能直接塞进 Vercel静态部署。若只使用定时归档，则不配置 `SEARXNG_URL` 也能展示真实历史发现。

## 真实数据保证

- 归档必须包含 `mock: false`；否则前端拒绝展示。
- 请求失败会显示错误，不生成演示结果。
- 每条内容标记为“搜索发现 / 搜索 Feed / 评论 Feed / 新闻 RSS”。
- 搜索发现不等于完整评论同步，也不承诺平台全量覆盖。

详细说明：[`docs/OPEN_SOURCE_SEARCH.md`](docs/OPEN_SOURCE_SEARCH.md)
