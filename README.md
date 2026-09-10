# 多 Agent 协作 - 全端测试用例生成系统

## 📋 项目概述

基于 ReAct 架构的智能测试用例生成系统，采用多 Agent 协作模式，自动分析需求文档（PRD）并生成高质量测试点与测试用例，覆盖客户端（App/Web/H5/通用）、后端服务、管理后台、端到端全链路。

## 🚀 快速开始

### 1. 环境准备

```bash
# 安装依赖
pip install -r requirements.txt

# 配置环境变量（复制 .env.example 为 .env 并填写）
cp .env.example .env
```

### 2. 配置 .env

| 变量 | 说明 | 是否必填 |
|---|---|---|
| `DASHSCOPE_API_KEY` | 阿里云百炼 API Key（文本 + 多模态） | 是 |
| `FEISHU_APP_ID` | 飞书自建应用 App ID | 是 |
| `FEISHU_APP_SECRET` | 飞书自建应用 App Secret | 是 |
| `FEISHU_OUTPUT_FOLDER` | 飞书目标文件夹 token | 是 |
| `FIGMA_ACCESS_TOKEN` | Figma Personal Access Token（读设计稿） | 可选 |

### 3. 启动服务

```bash
/opt/anaconda3/bin/python3 web_server.py
```

访问：http://localhost:5001

### 4. 使用流程

1. 在「一键全流程」Tab 粘贴飞书 PRD 链接
2. 选择需要生成的端（client/backend/admin）
3. 点击「开始生成」
4. 系统自动执行：需求分析 → 测试点生成 → 测试用例生成
5. 产物写入飞书文档并记录到产物查询

## 🏗️ 系统架构

```
飞书 PRD / Figma 链接
    │
    ▼
┌─────────────────┐
│  需求分析 Agent  │  → 结构化需求文档（Markdown + 飞书）
└─────────────────┘
    │
    ▼
┌─────────────────┐
│  测试点生成 Agent │  → 扁平 JSON 测试点（scope/platform/batch）
└─────────────────┘
    │
    ▼
┌─────────────────┐
│  测试用例生成 Agent│  → JSON 测试用例 + 飞书表格
└─────────────────┘
```

## ✨ 核心特性

- **多 Agent 协作**：需求分析 → 测试点 → 测试用例，三阶段流水线
- **飞书深度集成**：读取飞书 PRD、写入需求分析/测试点/测试用例文档
- **YAPI 接口注入**：自动提取 PRD 中的 YAPI 链接，结构化后注入 prompt
- **Figma 设计稿支持**：从 PRD 正文中提取 Figma URL，下载节点图片并通过多模态模型提取 UI 元素清单，注入测试点和测试用例生成
- **全端覆盖**：client（App/Web/H5/通用/E2E）、backend、admin
- **异步生成**：后台线程执行，前端轮询进度，避免 Flask worker 阻塞
- **熔断与降级**：LLM 连续失败自动降级，YAPI/飞书失败不阻塞主流程
- **任务级日志**：每个 task 独立日志文件 `logs/task_{id}.log`，支持 `/api/logs/export` 下载
- **缓存加速**：Figma 图片 URL→本地路径缓存、YAPI 接口 24h TTL 缓存、约束清单本地文件缓存
- **JSON 修复**：自动修复 LLM 输出的 trailing comma、裸单引号键、未转义换行等

## 📝 输入规范

### PRD 文档

- 使用飞书文档（docx/wiki）
- 文档中可直接粘贴 **Figma 设计稿链接**，系统会自动提取并分析
- 文档中可直接粘贴 **YAPI 接口链接**，系统会自动提取并注入

### Figma 链接

支持格式：

```
https://www.figma.com/design/{file_key}/{title}?node-id={node_id}
https://www.figma.com/file/{file_key}/{title}?node-id={node_id}
https://www.figma.com/proto/{file_key}/{title}?node-id={node_id}
```

私有文件需要在 `.env` 中配置 `FIGMA_ACCESS_TOKEN`。

## 🔧 主要 API

| 接口 | 说明 |
|---|---|
| `POST /api/pipeline_async` | 一键全流程（PRD → 需求分析 → 测试点 → 测试用例） |
| `POST /api/requirement` | 需求分析 |
| `POST /api/test_points` | 测试点生成 |
| `POST /api/generate_async` | 测试用例生成 |
| `GET /api/logs?task_id=xxx` | 拉取任务日志 |
| `GET /api/logs/export?task_id=xxx` | 导出任务日志 |
| `GET /api/logs/list` | 列出所有任务日志文件 |

## 📂 目录结构

```
PythonProject_testagent/
├── agents/                  # 业务 Agent
│   ├── requirement_analyzer/
│   ├── test_point_generator/
│   └── test_generator/
├── core/                    # 核心组件
│   ├── design_analyzer.py   # Figma/飞书设计稿分析
│   ├── feishu_client.py     # 飞书 API 客户端
│   ├── llm_client.py        # LLM 客户端
│   └── structured_doc.py    # 结构化文档
├── web/                     # 前端页面
│   └── index.html
├── web_server.py            # Flask 服务主入口
├── logs/                    # 应用日志 + 任务日志
└── temp_design/             # 设计稿缓存
```

## 🧪 运行测试

```bash
pytest tests/ -q
```

## 📌 注意事项

- Flask 启动必须使用 `use_reloader=False`，否则后台线程会被 reloader 中断
- 修改 `.env` 后必须重启 Flask 才能生效
- `.env` 文件已加入 `.gitignore`，请勿提交到版本控制
