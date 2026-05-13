.PHONY: help install test test-cov lint format clean run

help:  ## 显示帮助
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install:  ## 安装依赖
	pip install -r requirements.txt
	pip install ruff pytest pytest-cov

test:  ## 运行单元测试
	pytest tests/unit/ -v --tb=short

test-cov:  ## 测试 + 覆盖率报告
	pytest tests/unit/ -v --cov=app --cov-report=term-missing

lint:  ## 代码检查
	ruff check app/ tests/

format:  ## 代码格式化
	ruff format app/ tests/

clean:  ## 清理缓存
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	rm -rf .pytest_cache .mypy_cache

run:  ## 启动 Web 服务
	python3 -m uvicorn web.server:app --host 0.0.0.0 --port 8000
