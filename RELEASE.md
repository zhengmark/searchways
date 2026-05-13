# 发布流程

## 版本号规范

遵循 [语义化版本 2.0.0](https://semver.org/lang/zh-CN/)：

- `MAJOR.MINOR.PATCH`（如 `1.2.3`）
- MAJOR：不兼容的 API 变更
- MINOR：向下兼容的功能新增
- PATCH：向下兼容的问题修复

当前版本：**0.3.0**（Beta）

## 发布步骤

### 1. 准备发布分支

```bash
git checkout develop
git pull origin develop
git checkout -b release/v0.4.0
```

### 2. 更新版本号

- `pyproject.toml`：更新 `version` 字段
- `CHANGELOG.md`：将 `[Unreleased]` 改为 `[0.4.0]` + 日期

### 3. 最终测试

```bash
make test-cov
make lint
```

### 4. 合并到 main 并打标签

```bash
git checkout main
git merge --no-ff release/v0.4.0
git tag -a v0.4.0 -m "v0.4.0: 发布说明"
git push origin main --tags
```

### 5. 合并回 develop

```bash
git checkout develop
git merge --no-ff release/v0.4.0
git push origin develop
```

### 6. 创建 GitHub Release

在 GitHub Releases 页面创建新 Release，使用 `v0.4.0` 标签，勾选 "Generate release notes"。

### 7. 清理

```bash
git branch -d release/v0.4.0
git push origin --delete release/v0.4.0
```

## 回滚流程

如需回滚生产版本：

```bash
git checkout main
git revert --no-commit HEAD~1..HEAD
git commit -m "revert: 回滚到前一版本"
git push origin main
git tag -a v0.3.1 -m "v0.3.1: 回滚"
git push origin v0.3.1
```
