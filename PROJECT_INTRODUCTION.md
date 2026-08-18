# 多Agent协作 - 客户端测试用例生成系统

## 📋 项目概述

这是一个基于ReAct架构的智能测试用例生成系统，采用多Agent协作模式， 能够自动分析代码并生成高质量的客户端测试用例（Web UI、移动端、API）。
系统支持自然语言交互，可通过命令行或Web界面使用。

### 核心特性

- **多Agent协作架构**：需求分析Agent + 测试点生成Agent + 测试生成Agent + 编排器协调（3步流水线）
- **完整ReAct实现**：思考(Reasoning) + 行动(Acting)循环，包含记忆、工具、规划、反思四大模块
- **飞书文档集成**：需求分析结果自动写入飞书文档（指定文件夹），支持在线协作评审
- **下游Agent数据传递**：需求分析Markdown通过结构化解析自动传递给测试点/测试生成Agent
- **自然语言交互**：支持中文/英文自然语言指令，智能解析用户意图
- **多LLM提供商支持**：阿里云百炼(DashScope)、DeepSeek、OpenAI GPT-4，三级优先级自动切换
- **多测试类型覆盖**：Web UI (Playwright/Selenium)、移动端 (Appium)、API (requests)
- **三渠道接入**：CLI交互式 + Web图形界面 + 飞书机器人对话（均为Agent调用渠道）
- **降级容错机制**：LLM失败时自动降级到规则模板，保证系统可用性

---

## 🏗️ 系统架构

### 分层架构设计

```
PythonProject_testagent/
├── agents/                        # 业务智能体层
│   ├── base_agent.py             # Agent基类（ReAct标准接口）
│   ├── _prompt_utils.py          # Prompt加载工具（共享）
│   ├── requirement_analyzer/     # 需求分析Agent
│   │   ├── agent.py              # 分析逻辑 + 飞书文档写入
│   │   └── prompts.md            # 需求分析提示词（纯文本，可直接编辑）
│   ├── test_point_generator/     # 测试点生成Agent
│   │   ├── agent.py              # 需求→测试场景转化
│   │   ├── generate_scenarios.md # 场景生成提示词（纯文本）
│   │   └── prd_to_testpoints.md  # PRD转测试点提示词（纯文本）
│   ├── test_generator/           # 测试代码生成Agent（ReAct）
│   │   ├── agent.py              # 场景→可执行测试代码
│   │   ├── generate_test.md      # 通用测试生成提示词（纯文本）
│   │   ├── web_test.md           # Web测试提示词（纯文本）
│   │   ├── mobile_test.md        # 移动端测试提示词（纯文本）
│   │   └── api_test.md           # API测试提示词（纯文本）
│   └── orchestrator/             # Agent编排器
│       └── agent.py              # 3步流水线协调
│
├── core/                          # 框架核心组件
│   ├── llm_client.py             # LLM客户端（百炼/DeepSeek/OpenAI）
│   ├── feishu_client.py          # 飞书API客户端（文档读写，token自动刷新）
│   ├── feishu_bot.py             # 飞书机器人（意图路由 + Agent调度）
│   ├── memory/                   # 记忆模块
│   │   ├── base_memory.py        # 记忆基类
│   │   ├── working_memory.py     # 工作记忆（短期）
│   │   └── long_memory.py        # 长期记忆
│   ├── tools/                    # 工具库
│   │   ├── registry.py           # 工具注册中心
│   │   └── tool_defs/            # 工具定义
│   ├── planner.py                # 任务规划器
│   └── reflector.py              # 反思评估器
│
├── utils/                         # 通用工具函数
│   ├── code_analyzer.py          # 代码结构分析器
│   └── template_loader.py        # 测试模板加载器
│
├── prompts/                       # 提示词仓库
│   └── base_prompts.py           # 基础提示词模板
│
├── generated_tests/               # 测试代码输出
├── generated_requirements/        # 需求分析Markdown输出
├── web/                           # Web前端资源
│   └── index.html                # 前端页面
│
├── main.py                        # CLI入口（3步流水线）
├── web_server.py                 # Flask Web服务（含/api/pipeline）
├── requirements.txt              # Python依赖
└── .env                          # 环境变量配置
```

---

## 🤖 Agent详解

### 1. BaseAgent（Agent基类）

**职责**：定义标准ReAct架构接口

**核心组件**：
- **大脑 (LLM)**：智能决策引擎
- **记忆 (Memory)**：工作记忆 + 长期记忆
- **工具 (Tools)**：可扩展工具库
- **规划 (Planner)**：任务分解与调度
- **反思 (Reflector)**：结果评估与经验积累

**标准执行流程**：
```
1. 加载长期记忆 → load_long_term(query)
2. 制定计划 → planner.make_plan(task_type, context)
3. ReAct循环：
   while not plan.finished():
       - LLM思考下一步 → _think(step, memory)
       - 执行动作 → tools.execute(action)
       - 记录结果 → memory.add_working(result)
4. 反思评估 → reflector.evaluate(final_result)
5. 保存经验 → memory.save_experience()
```

### 2. RequirementAnalyzer（需求分析Agent）

**职责**：从源代码中提取测试需求和场景，并将结果写入飞书文档 + 传递给下游Agent

**工作流程**：
```
输入：file_path + test_type
  ↓
代码结构分析（CodeAnalyzer）
  ↓
LLM提取功能点 & 测试点
  ↓
LLM生成测试场景列表
  ↓
保存Markdown到 generated_requirements/
  ↓
写入飞书文档（指定文件夹 ECD7fz3gtlKapJdwY8kcsFPMnvh）
  ↓
输出：requirements + test_scenarios + markdown + feishu_doc
```

**关键方法**：
- `execute(input_data)`：结构化分析入口（自动触发飞书写入）
- `process_query(query)`：自然语言处理入口
- `_save_and_publish(markdown, doc_title)`：本地保存 + 飞书文档写入
- `_extract_requirements_with_llm()`：LLM智能提取
- `_generate_scenarios_with_llm()`：场景智能生成

**双输出机制**：
- **飞书文档**：通过 `FeishuClient.create_doc(folder_token)` 写入飞书
- **下游传递**：返回 `markdown` 字段，由编排器解析后传入下游Agent

### 3. TestPointGenerator（测试点生成Agent）

**职责**：将需求分析结果转化为测试场景描述

**工作流程**：
```
输入：requirements（结构化需求列表） + markdown（原始Markdown）
  ↓
LLM解析需求 + 生成测试场景
  ↓
输出：测试场景列表（JSON）
```

**关键方法**：
- `execute(input_data)`：结构化输入（requirements列表 + test_type）
- `process_query(query, context)`：自然语言输入（自动提取需求，可独立调用）

### 4. TestGeneratorAgent（测试生成Agent）

**职责**：根据测试场景生成可执行的测试代码

**工作流程**：
```
输入：test_point（单个场景） + source_file
  ↓
构建prompt（含框架选择）
  ↓
LLM生成测试代码
  ↓
解析响应 & 提取代码块
  ↓
保存到 generated_tests/
  ↓
输出：test_case + file_path
```

**支持的测试框架**：
- **Playwright**：现代Web自动化（默认）
- **Selenium**：传统Web自动化
- **Appium**：移动端自动化
- **requests + pytest**：API测试

**自然语言解析能力**：
```
# 示例查询
"为login函数生成Web异常测试"
"检查get_user_info的API超时处理"
"为swipe手势生成移动端边界测试"

# 自动提取参数
{
  "function": "login",
  "scenario_type": "error_handling",
  "priority": "high",
  "test_points": ["异常处理"],
  "source_file": "auth.py"
}
```

### 5. AgentOrchestrator（编排器）

**职责**：协调3个Agent完成端到端流水线

**工作流步骤**：
```
Step 1: RequirementAnalyzer 分析代码 → 写入飞书 + 返回Markdown
  ↓
Step 2: _extract_requirements 解析Markdown为结构化需求列表
  ↓
Step 3: TestPointGenerator 接收需求 → 生成测试场景（JSON）
  ↓
Step 4: TestGeneratorAgent 逐个场景生成可执行测试代码
  ↓
Step 5: 汇总生成结果 & 状态报告
```

**配置选项**：
- `test_type`：测试类型（web/mobile/api）
- `api_key` + `base_url`：LLM配置，确保全链路传递
- `llm_client` + `feishu_client`：可复用客户端实例
- `generate_all`：是否生成所有场景（或仅高优先级）

---

## 🔧 核心模块

### 1. LLMClient（LLM客户端）

**多提供商支持**：
```python
# 阿里云百炼DashScope配置（最高优先级）
DASHSCOPE_API_KEY=sk-xxx
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
DASHSCOPE_MODEL=qwen-plus

# DeepSeek配置
DEEPSEEK_API_KEY=sk-6969...
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash

# OpenAI配置
OPENAI_API_KEY=sk-xxx
OPENAI_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4
```

**自动识别逻辑**：
- 优先级：百炼(DashScope) > DeepSeek > OpenAI
- 根据 `base_url` 自动选择模型
- `base_url` 全链路传递，确保各Agent连接正确的API端点

**降级机制**：
- LLM不可用时自动切换到模板生成（通过 `utils/template_loader.py` 加载降级模板）
- 保证系统在无API Key时仍可运行

### 2. Memory System（记忆系统）

**三层记忆架构**：
- **工作记忆 (Working Memory)**：当前任务的临时存储
- **长期记忆 (Long-term Memory)**：JSON文件持久化经验数据，存储目录通过 `MEMORY_DIR` 环境变量配置（默认 `memory_data/`）
- **记忆检索**：基于相关性加载历史经验

**记忆内容**：
```json
{
  "task_type": "test_generation",
  "success": true,
  "timestamp": "2026-08-07 10:30:00",
  "details": ["代码格式正确", "断言完整"]
}
```

### 3. ToolRegistry（工具注册中心）

**内置工具**：
- `code_analyzer`：代码结构分析
- `file_writer`：文件写入
- `test_runner`：测试执行

**自定义工具注册**：
```python
tools.register(
    name='save_test',
    func=save_test_tool,
    description='保存测试代码到文件'
)
```

### 4. Planner & Reflector

**Planner（规划器）**：
- 将复杂任务分解为子步骤
- 跟踪执行进度
- 动态调整计划

**Reflector（反思器）**：
- 评估生成结果质量
- 识别潜在问题
- 记录改进建议

---

## 🚀 使用方式

### 方式1：CLI交互式（命令行）

#### 完整工作流模式
```bash
python main.py

# 选择模式：1（完整工作流）
# 选择测试类型：1/2/3（web/mobile/api）
# 输入目标文件路径：demo/login.py
# 输入API Key（可选，留空使用模板模式）
# 选择生成策略：all（全部）或 high（仅高优先级）
```

#### 单独Agent调用模式
```bash
python main.py

# 选择模式：2（单独调用）
# 输入命令：
>>> req 分析demo/login.py的测试需求
>>> tp  根据login功能生成测试场景
>>> gen 为login函数生成Web异常测试
>>> quit
```

**可用命令**：
- `req <查询>`：调用需求分析Agent
- `tp <查询>`：调用测试点生成Agent
- `gen <查询>`：调用测试生成Agent
- `quit`：退出

### 方式2：Web图形界面

#### 启动服务
```bash
python web_server.py --port 5001
```

访问：http://localhost:5001

#### API接口

**1. 完整流水线（推荐）**
```http
POST /api/pipeline
Content-Type: application/json

# 模式A：分析代码文件
{
  "file_path": "demo/login.py",
  "test_type": "web",
  "generate_all": true
}

# 模式B：自然语言查询
{
  "query": "分析登录功能并生成测试",
  "test_type": "web"
}
```

**2. 单独需求分析**
```http
POST /api/requirement
Content-Type: application/json

{
  "query": "分析demo/login.py的测试需求"
}
```

**3. 测试点生成**（支持结构化输入或自然语言）
```http
POST /api/test_points
Content-Type: application/json

# 模式A：结构化需求列表
{
  "requirements": [{"function": "login", "complexity": "medium", "test_points": ["参数验证"]}]
}

# 模式B：自然语言
{
  "query": "根据login功能生成测试场景"
}
```

**4. 单独测试生成**
```http
POST /api/generate
Content-Type: application/json

{
  "query": "补充说明（可选）",
  "requirement_doc": "需求文档内容",
  "context": {
    "source_file": "demo/login.py",
    "function": "login"
  }
}
```

**5. 状态查询**
```http
GET /api/status
```

**6. 飞书机器人事件回调**
```http
POST /api/feishu/event
Content-Type: application/json

# 飞书平台自动推送，无需手动调用
# URL验证时返回 challenge，消息事件时异步处理并回复
```

### 方式3：飞书机器人对话

飞书应用绑定的机器人，在飞书私聊/群聊中发送自然语言消息，系统自动识别意图并调用对应Agent返回结果。

#### 启用条件
- `.env` 中配置 `FEISHU_APP_ID` 和 `FEISHU_APP_SECRET`（飞书客户端初始化成功即自动启用）
- 飞书开发者后台添加**机器人**能力
- 添加事件订阅 `im.message.receive_v1`，回调地址填 `https://<域名>/api/feishu/event`
- 授予 `im:message` 权限

#### 使用方式
直接在飞书中发消息：
- 「分析demo/login.py的测试需求」→ 调用需求分析Agent
- 「根据login功能生成测试场景」→ 调用测试点生成Agent
- 「为login函数生成Web测试用例」→ 调用测试代码生成Agent
- 「帮助」→ 查看使用说明

意图识别：关键词规则匹配（零延迟）→ LLM分类（兆底）

---

## 📝 示例演示

### 示例1：分析登录功能测试需求

**输入**：
```python
req_agent = RequirementAnalyzer(llm_client=api_key)
result = req_agent.process_query("分析demo/login.py的测试需求")
```

**输出**：
```json
{
  "requirements": [
    {
      "function": "login",
      "params": ["username", "password"],
      "complexity": "medium",
      "test_points": ["UI交互", "参数验证"]
    }
  ],
  "test_scenarios": [
    {
      "function": "login",
      "scenario": "normal",
      "description": "测试login正常流程",
      "priority": "high",
      "test_points": ["UI交互", "参数验证"]
    },
    {
      "function": "login",
      "scenario": "error_handling",
      "description": "测试login异常处理",
      "priority": "high",
      "test_points": ["错误恢复", "降级处理"]
    }
  ]
}
```

### 示例2：生成测试代码

**输入**：
```python
gen_agent = TestGeneratorAgent(api_key=api_key, test_type='web')
result = gen_agent.process_query("为login函数生成Web异常测试")
```

**输出**：
```python
# generated_tests/test_login_login_error_handling.py
import pytest
from playwright.sync_api import Page, expect

def test_login_error_handling(page: Page):
    """测试登录异常处理"""
    page.goto("http://localhost:3000/login")
    
    # 测试无效用户名
    page.fill("#username", "invalid_user")
    page.fill("#password", "wrong_pass")
    page.click("#submit")
    
    # 验证错误提示
    expect(page.locator(".error-message")).to_be_visible()
    expect(page.locator(".error-message")).to_contain_text("用户名或密码错误")
```

### 示例3：完整流水线

```python
orchestrator = AgentOrchestrator(
    test_type='web',
    api_key=api_key,
    base_url=base_url,
    llm_client=llm_client,
    feishu_client=feishu_client
)
result = orchestrator.execute_workflow(
    file_path="demo/login.py",
    generate_all=True
)

# 流水线执行过程：
# Step 1: RequirementAnalyzer 分析代码 → 写入飞书文档
# Step 2: _extract_requirements 解析Markdown为结构化需求
# Step 3: TestPointGenerator 生成测试场景
# Step 4: TestGeneratorAgent 生成可执行测试代码
#
# 输出：
# ✓ 需求分析完成，飞书文档已创建
# ✓ 解析到 3 个功能点
# ✓ 生成 7 个测试场景
# ✓ 流水线完成！共生成 7 个测试文件
```

---

## ⚙️ 环境配置

### 1. 安装依赖
```bash
pip install -r requirements.txt
```

### 2. 配置环境变量
```bash
cp .env.example .env
vim .env
```

**配置示例**：
```env
# 阿里云百炼DashScope（最高优先级）
DASHSCOPE_API_KEY=sk-xxx
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
DASHSCOPE_MODEL=qwen-plus

# DeepSeek配置
DEEPSEEK_API_KEY=sk-6969...
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash

# 或 OpenAI配置
OPENAI_API_KEY=sk-xxx
OPENAI_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4

# 飞书配置
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx
FEISHU_OUTPUT_FOLDER=your_folder_token
FEISHU_DOMAIN=your_company.feishu.cn

# 记忆文件存储目录（默认 memory_data/）
MEMORY_DIR=memory_data
```

### 3. 运行测试
```bash
pytest generated_tests/ -v
```

---

## 🎯 技术亮点

### 1. ReAct架构实现
- **Reasoning（推理）**：LLM智能决策下一步动作
- **Acting（行动）**：调用工具执行具体任务
- **循环优化**：通过反思不断改进结果

### 2. 自然语言理解
- 智能解析中英文混合查询
- 自动提取函数名、测试类型、优先级等参数
- 支持模糊匹配和上下文推断

### 3. 降级容错机制
```
LLM调用成功 → 智能生成高质量代码
    ↓ 失败
规则匹配 → 模板化生成基础代码
    ↓ 失败
返回错误提示 → 引导用户修正输入
```

### 4. 经验积累系统
- 每次生成后自动评估质量
- 保存成功经验供后续参考
- 持续优化生成策略

### 5. 多LLM提供商适配
- 统一的LLMClient接口
- 自动识别API提供商（百炼/DeepSeek/OpenAI）
- `base_url` 全链路传递，无缝切换不同模型

### 6. 飞书文档集成 + 机器人交互
- 需求分析结果自动写入飞书文档
- 支持在线协作评审
- 通过 `FEISHU_OUTPUT_FOLDER` 环境变量指定目标文件夹
- 飞书域名通过 `FEISHU_DOMAIN` 环境变量配置（默认 `xueqiu.feishu.cn`）
- `access_token` 自动过期刷新（提前60秒续期，避免长时间运行后失效）
- 飞书机器人支持自然语言对话，自动识别意图调用对应Agent
- 异步处理消息（避免飞书 3 秒超时限制），`event_id` 去重防止重复处理

### 7. Prompt与代码分离
- 每个Agent目录下直接存放 `.md` 纯文本文件，整个文件即prompt指令
- `agents/_prompt_utils.py` 为共享加载器，负责读取md、校验占位符、注入参数
- 编辑prompt无需理解Python语法，不会误删代码
- 启动时自动校验占位符是否存在，缺失即报错

---

## 📊 项目统计

- **代码行数**：约 2500+ 行
- **核心模块**：5个Agent + 6个核心组件
- **支持测试类型**：3种（Web/Mobile/API）
- **支持LLM提供商**：3种（百炼DashScope/DeepSeek/OpenAI）
- **交互方式**：3种（CLI/Web/飞书机器人）
- **飞书集成**：需求分析结果自动写入飞书文档 + 飞书机器人对话交互

---

## 🔮 未来扩展方向

1. **更多测试框架**：支持Cypress、TestNG等
2. **视觉测试**：集成截图对比功能
3. **性能测试**：自动生成负载测试脚本
4. **CI/CD集成**：对接Jenkins、GitHub Actions
5. **测试报告**：生成可视化测试报告
6. **团队协作**：支持多人协作和需求评审

---

## 📄 许可证

本项目仅供学习和研究使用。

---

## 👥 贡献指南

欢迎提交Issue和Pull Request！

**开发建议**：
- 遵循PEP 8代码规范
- 添加单元测试覆盖新功能
- 更新文档说明变更内容

---

**最后更新时间**：2026-08-12
