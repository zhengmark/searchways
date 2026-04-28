# AI 本地路线智能规划 Agent

美团 AI 黑客松参赛项目 ——「现在就出发：AI本地路线智能规划」

## 项目介绍

用户用自然语言描述出行需求，Agent 自动规划一条结合 POI 数据、用户评价和个人偏好的本地路线方案。不只是导航，而是一段有温度的出行体验设计。

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填入真实的 API Key
```

### 3. 运行

```bash
python main.py
```

## 技术栈

- **LLM**: OpenAI GPT-4o（Tool Use / Function Calling）
- **地图**: 高德地图开放平台
- **POI & 评价**: 大众点评开放平台
- **框架**: 原生 OpenAI Python SDK，无额外 Agent 框架

## 项目结构

```
├── main.py              # CLI 入口
├── agent/
│   ├── core.py          # Agent 主循环（Tool Use 循环）
│   ├── config.py        # 环境变量管理
│   ├── models.py        # 数据结构（Pydantic）
│   └── tools/
│       ├── poi.py       # POI 搜索工具
│       ├── routing.py   # 路径计算工具
│       └── reviews.py   # 评论获取工具
├── requirements.txt
└── .env.example
```

## 示例输入

```
今天下午带我妈逛逛，她喜欢安静的地方，不想走太多路，在北京朝阳区
```
