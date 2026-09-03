# 开源公开舆情搜索层

该目录为 `deepfeed` 增加零软件许可费的跨平台公开内容发现能力。

## 已接入

- SearXNG：通过 `site:` 查询发现抖音、小红书、B站、TapTap、微信公众号中已被搜索引擎索引的公开页面。
- RSS/Atom：继续保留公开新闻和行业媒体 Feed。
- 统一归档：结果合并到 `data/live.json`，保留 `firstSeenAt`、`lastSeenAt`、来源、平台、查询词和 `accessLevel`。
- 非 mock 校验：归档固定为 `mock:false`；任务失败时不生成演示数据。
- 定时运行：`.github/workflows/collect-open-source-stack-v2.yml` 每小时执行，也可手动触发。

## 本地运行

```bash
pip install -r opensource-search/requirements.txt

docker run -d --name tide-searxng \
  -p 8080:8080 \
  -v "$PWD/opensource-search/searxng-settings.yml:/etc/searxng/settings.yml:ro" \
  searxng/searxng:latest

SEARXNG_BASE_URL=http://127.0.0.1:8080 \
DEEPFEED_CONFIG=opensource-search/config.json \
DEEPFEED_OUTPUT=data/live.json \
python opensource-search/collect.py
```

## 数据口径

`accessLevel=search_discovered` 表示只发现标题、摘要和链接，不代表拿到了页面全部评论。完整评论仍需官方接口、合法 Feed、用户提交或逐站审核的连接器。

## 免费与成本

SearXNG 和当前采集代码没有按次搜索费用。若使用 GitHub Actions 的临时容器，适合小时级扫描；若需要用户随时点击即时搜索，需要在现有服务器、NAS 或开发机上让 SearXNG 持续在线。此时没有软件许可费，但会占用机器、电力、带宽和运维资源。

## 可选扩展

完整 Docker 接入包还包括：

- RSSHub：为支持的公开来源生成 Feed；每条路由需要单独验证。
- Trafilatura：对白名单公开网页提取正文。
- changedetection.io：监控已知页面更新。
- FastAPI：提供 `/search`、`/health`、`/archive` 接口给 Web 工作台。

这些组件均与登录态抓取、验证码绕过、个人账号池无关。
