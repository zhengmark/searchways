# Changelog

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Added
- 工业级项目架构：CI/CD、分支保护、社区健康文件
- GitHub Actions CI 流水线（lint + test + build）
- Dependabot 自动依赖更新
- CODE_OF_CONDUCT、CONTRIBUTING、SECURITY、SUPPORT
- PR 模板、Issue 模板、CODEOWNERS
- Makefile、.editorconfig、pyproject.toml
- CHANGELOG、ROADMAP、RELEASE、MAINTAINERS
- Git Flow 分支策略（main/develop/feat/fix/hotfix/release）

## [0.3.0] — 2026-05-13

### Added
- POI 检索管线修复（聚类查询 + 关键词/预算/非游客过滤）
- InputEnricher 默认城市层
- ConstraintModel 约束模型
- SafeGraph + 坐标验证
- 超能力中文技能框架 v1.3.0（20 个 skills）

### Changed
- graph_planner shortest_path 使用非 clamp 投影 + 垂距加权
- tool_build_route 终点纳入 stops

## [0.2.0] — 2026-05-08

### Added
- 统一 Agent 架构（替代多 Agent 串行架构）
- 用户登录系统（注册/登录/JWT）
- 前端去 mock 化，接入真实 API
- Docker 容器化支持
- Leaflet 交互地图增强
- 个性化推荐引擎（走廊感知评分）

### Fixed
- 17 项系统修复
- 走廊查询性能修复

## [0.1.0] — 2026-04-28

### Added
- 初始版本：AI 本地路线智能规划
- LLM 工具调用循环（geocode → query_clusters → build_route → 解说）
- Mermaid 路线图 + Leaflet 交互地图输出
- 聚类预计算 (~15x 建图优化)
- 10 用户多轮对话测试
