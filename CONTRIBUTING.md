# 贡献指南

感谢你对「现在就出发」项目的关注！

## 开发流程

1. **Fork 本仓库** 或从 `develop` 分支拉出新分支
2. 分支命名：`feat/your-feature` 或 `fix/your-bug`
3. 编写代码（遵循 TDD：先写测试，再写实现）
4. 确保测试通过：`make test`
5. 运行 lint：`make lint`
6. 提交代码：
   ```bash
   git commit -m "feat(scope): 简要描述"
   ```
7. 推送到 GitHub 并发起 Pull Request 到 `develop` 分支
8. 等待 CI 通过 + 代码审查

## 分支策略

本项目采用 Git Flow 模型：

```
main        ← 生产环境（受保护，需要 PR + 审查）
develop     ← 开发主线（受保护，需要 CI 通过）
feat/*      ← 功能分支（从 develop 拉出，合回 develop）
fix/*       ← Bug 修复（从 develop 拉出）
hotfix/*    ← 紧急修复（从 main 拉出，同时合回 main 和 develop）
release/*   ← 发布分支
```

## 提交规范

遵循 [Conventional Commits](https://www.conventionalcommits.org/zh-hans/)：

| 类型 | 说明 |
|------|------|
| feat | 新功能 |
| fix | Bug 修复 |
| refactor | 重构 |
| docs | 文档更新 |
| test | 测试相关 |
| ci | CI/CD 配置 |
| chore | 构建/工具/依赖 |

示例：
```
feat(route): 支持多途经点路线规划

- 新增 RouteStop 数据模型
- 支持最多 5 个途经点
- 添加途经点排序算法

Closes #42
```

## 代码风格

- Python 3.10+
- 使用 ruff 进行代码检查和格式化（配置见 pyproject.toml）
- 运行 `make format` 自动格式化

## 测试

- 框架：pytest
- 单元测试目录：`tests/unit/`
- 所有新功能必须包含单元测试
- 运行：`make test` 或 `make test-cov`（含覆盖率）

## 文档

- 架构文档在 `docs/` 目录
- 重大变更需更新 `CHANGELOG.md`
- API 变更需更新 `CLAUDE.md` 中的快速参考

## 问题反馈

- Bug 报告：使用 Bug Report Issue 模板
- 功能请求：使用 Feature Request Issue 模板
- 安全问题：请勿公开提交 Issue，参见 `SECURITY.md`
