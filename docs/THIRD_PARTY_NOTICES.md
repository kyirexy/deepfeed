# 第三方组件说明

本仓库通过容器或 HTTP 接口组合第三方开源项目，不复制其源码到本应用中。部署者应自行核对并遵守各项目的最新许可证及目标站点规则。

- SearXNG — GNU Affero General Public License v3.0
- RSSHub — GNU Affero General Public License v3.0
- changedetection.io — 以其仓库当前许可证为准
- FastAPI — MIT
- httpx — BSD-3-Clause
- Redis — 以选用镜像版本的当前许可证为准

若修改 AGPL 组件并通过网络向用户提供其功能，应单独评估相应源码提供义务。生产环境建议把 SearXNG、RSSHub 与本应用作为独立服务部署，并保留各自版权和许可证文件。
