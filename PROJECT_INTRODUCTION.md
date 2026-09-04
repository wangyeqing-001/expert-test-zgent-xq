# 多Agent协作 - 全端测试用例生成系统

## 📋 项目概述

这是一个基于ReAct架构的智能测试用例生成系统，采用多Agent协作模式，能够自动分析需求文档并生成高质量的测试点与测试用例，覆盖客户端（App/Web/H5/通用）、后端服务、管理后台、端到端全链路。
系统支持自然语言交互，可通过命令行或Web界面使用。

### 核心特性

- **多Agent协作架构**：需求分析Agent + 测试点生成Agent + 测试生成Agent + 编排器协调（3步流水线）
- **完整ReAct实现**：思考(Reasoning) + 行动(Acting)循环，包含记忆、工具、规划、反思四大模块
- **飞书文档全链路集成**：需求分析与测试点均自动写入飞书文档（结构化JSON直写原生块），测试点文档含溯源链接（需求文档+需求分析飞书链接），支持在线协作评审
- **测试点扁平JSON架构**：AI输出扁平数组（每条带scope字段，7选1），代码自动按scope→platform映射分组+分批；PRD直提（原始需求主材料+约束清单辅助门控）与代码分析双链路并行
- **JSON测试用例自动生成**：TestGenerator按 platform 路由3类 prompt，每条测试点生成≥3条（P0/P1）或≥2条（P2）JSON测试用例（含test_module/test_point/test_scenario/test_data/test_steps/expected_results/priority/status/remarks 9字段），按 test_module 分组后 H1→H2→6列表格写入飞书
- **按端大类整合飞书文档**：客户端(App/Web/H5/通用/E2E)自动合并为1个文档存入客户端文件夹，backend→后端文件夹，admin→后台文件夹；task最多生成3个飞书文档（视需求覆盖端而定）
- **异步生成 + 前端轮询**：新增 `/api/generate_async`（提交立即返回gen_task_id，后台线程执行）+ `/api/generate_status`（轮询进度/日志/结果）；前端提交后轮询进度面板（x/y批 + 最近日志），避免Flask worker被长耗时请求占用
- **LLM截断自适应策略**：`_is_truncated` 检测JSON闭合/代码块闭合/结尾断句；截断后**递归拆子批**（binary split）而非翻倍max_tokens，最小粒度2点，最大深度4层；安全断言 `_MAX_POINTS_PER_BATCH=5` 兜底
- **Token预算优化**：主提取 max_tokens 16000→8000，testpoints_table 16000→4000；所有平台 batch_size 统一为3
- **YAPI出参结构化摘要**：`_summarize_res_schema` 将 response_schema（dict/str）转为字段名+类型+必填+描述(50字)的结构化摘要，替代800字符硬截断；整体YAPI截断6000→8000，单接口200-300字符可覆盖20+接口
- **下游数据传递**：测试点JSON（含scope/module/platform等字段+TestBatch批次）落盘，下游 TestGenerator 按 platform 路由对应 prompt 分批生成用例
- **测试用例 prompt 3类合并**：client_test.md（App/Web/H5/通用/E2E共性+framework参数化）+ admin_test.md + backend_test.md，各端框架选型/断言重点通过参数注入，TestGenerator 按 `batch.platform` 自动匹配
- **自然语言交互**：支持中文/英文自然语言指令，智能解析用户意图
- **多LLM提供商支持**：阿里云百炼(DashScope)、DeepSeek、OpenAI GPT-4，三级优先级自动切换
- **全端测试覆盖**：客户端 App (Appium)、Web/H5 (Playwright)、后端服务 (requests+pytest)、管理后台 (Playwright)、跨端 E2E (Playwright+requests)
- **多渠道接入**：CLI交互式 + Web图形界面 + 飞书机器人对话 + 飞书链接独立脚本（均为Agent调用渠道）
- **Web 体验优化**：实时日志面板（增量轮询展示）、分析期间按钮置灰防重复提交、需求分析历史记录（持久化原始文档链接+生成飞书文档链接+任务ID，分页加载：默认3条+每次加载更多5条，可点击回溯）
- **YAPI 接口数据集成**：需求分析阶段从主文档+关联文档中提取 YAPI 链接（清洗前提取，不丢失），绑定 task_id 存入 history.json；测试点生成时通过内部接口 `getInterfaceData` 拉取接口详情（路径/方法/入参/出参），标准化映射后注入 prompt，AI 为每个接口打标签（本次新增/修改/存量复用-建议回归/存量复用-无需测试），不丢弃任何接口，输出接口索引表供测试人员 review
- **飞书日志上下文标注**：日志区分"主文档"与"关联文档"拉取，关联文档失败自动降级为WARNING并跳过（不影响主文档分析）
- **降级容错机制**：LLM失败时自动降级到确定性渲染/规则模板，保证系统可用性；YAPI 接口拉取失败（404/鉴权/超时）自动跳过不阻塞，无接口数据时 AI 仅基于 PRD 正常工作
- **Flask稳定性**：`debug=False` + `use_reloader=False`，防止 reloader 重启丢失后台线程；所有 `_agent_lock` 用 `with` 上下文管理器确保异常时释放

---

## 🏗️ 系统架构

### 分层架构设计

```
PythonProject_testagent/
├── agents/                        # 业务智能体层
│   ├── base_agent.py             # Agent基类（ReAct标准接口）
│   ├── _prompt_utils.py          # Prompt加载工具（共享，占位符校验+注入）
│   ├── requirement_analyzer/     # 需求分析Agent
│   │   ├── agent.py              # 双链路分析 + 飞书struct直写 + 原始文档透出
│   │   ├── prompts.md            # 结构化JSON输出提示词（主链路）
│   │   └── prompts_markdown.md   # Markdown降级提示词（灰度兜底）
│   ├── test_point_generator/     # 测试点生成Agent
│   │   ├── agent.py              # 双链路：prd直提（扁平JSON+scope）+ code场景
│   │   ├── prd_to_testpoints.md  # prd直提提示词（主辅材料+门控指令+scope字段）
│   │   └── constraints_extract.md# 约束清单提取提示词（防遗漏索引）
│   ├── test_generator/           # 测试用例JSON生成Agent（截断自适应拆批）
│   │   ├── agent.py              # 场景→可执行测试代码（按platform路由prompt）
│   │   ├── client_test.md       # 客户端测试用例JSON提示词（App/Web/H5/通用/E2E，framework参数化）
│   │   ├── admin_test.md        # 管理后台测试用例JSON提示词
│   │   └── backend_test.md     # 后端服务测试用例JSON提示词
│   └── orchestrator/             # Agent编排器
│       └── agent.py              # 3步流水线协调
│
├── core/                          # 框架核心组件
│   ├── llm_client.py             # LLM客户端（百炼/DeepSeek/OpenAI）
│   ├── feishu_client.py          # 飞书API客户端（struct直写原生块，token自动刷新，markdown超链接）
│   ├── feishu_bot.py             # 飞书机器人（意图路由 + Agent调度）
│   ├── structured_doc.py         # 结构化文档模型（8种业务节点 ↔ 飞书原生块）
│   ├── dispatcher.py             # 通用意图路由器（三渠道共用）
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
├── generated_tests/               # 测试代码输出
├── generated_requirements/        # 需求分析文档输出（含 history.json 历史记录索引）
├── generated_testpoints/          # 测试点输出（按端分节表格.md + JSON落盘）
├── tests/                         # 单元测试（structured_doc等）
├── demo/                          # 示例被分析代码
├── web/                           # Web前端资源
│   └── index.html                # 前端页面（需求分析/测试点/测试生成 + 实时日志 + 历史记录分页）
│
├── main.py                        # CLI入口（完整流水线 + 单独Agent调用）
├── run_requirement.py            # 独立脚本：飞书文档链接 → 需求分析（一步到位）
├── web_server.py                 # Flask Web服务（/api/requirement·/test_points·/generate·/generate_async·/generate_status·/pipeline·/feishu·/status·/logs·/history）
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

**职责**：分析代码文件或PRD/飞书需求文档，产出结构化需求分析书，写入飞书并向下游透出原始文档

**输入支持**：
- 代码文件路径（CodeAnalyzer结构分析）
- PRD文本（粘贴/上传）
- 飞书文档链接（自动拉取内容，支持 docx/wiki）

**双链路灰度架构**：
```
新链路（USE_STRUCT_OUTPUT=True）：prompts.md 输出业务JSON
  → parse_struct_json校验 → struct节点直写飞书原生块
  → 失败降级：重新调LLM用prompts_markdown.md产出Markdown（旧链路）
旧链路：prompts_markdown.md → Markdown解析写飞书
```

**工作流程**：
```
输入：file_path / PRD文本 / 飞书链接
  ↓
文档清洗（去图片占位/@提及/URL行/模板套话，幂等）
  ↓
LLM双链路生成分析文档（h1-h3/段落/列表/表格/code 8种节点）
  ↓
保存 generated_requirements/ + 飞书struct直写（FEISHU_OUTPUT_FOLDER）
  ↓
输出：markdown + local_path + feishu_url + raw_content + metadata
```
**关键输出字段**：
- `raw_content`：清洗后的原始需求全文（供测试点prd直提作主材料）
- `metadata.source`：`prd_document` / `feishu_doc` / 代码（下游据此选链路）
- `metadata.title`：动态标题（从文档提取，传递给飞书文档命名）

### 3. TestPointGenerator（测试点生成Agent）

**职责**：从需求提取原子化测试点，产出按端分节表格（飞书/本地）+ JSON落盘供下游用例生成分批消费

**双链路架构（按需求来源自动选择）**：

```
链路1：prd直提（source='prd'，需求来自PRD/飞书文档）
  原始PRD全文（主材料，唯一事实来源）
    ├→ 分支A：constraints_extract.md 提取约束清单（防遗漏索引）
    └→ 主流程：prd_to_testpoints.md
        辅助材料注入门控：不作事实来源/无视推测/冲突以PRD为准
        YAPI 接口数据注入（标准化结构：api_path/method/params/response_schema）
        AI 为每个接口打4类标签（新增/修改/复用-回归/复用-无需测试），不丢弃
      ↓ 一次LLM直出 JSON 对象 {test_points: [...], interface_index: [...]}
      ↓ 代码按 _SCOPE_PLATFORM_MAP 白名单映射 scope→platform + 非法scope跳过
      ↓ _split_into_batches：同端内按 max_per_batch 切批 → List[TestBatch]
      ↓ _publish_points：飞书文档正文（溯源链接 + 概述 + 按端H2分节表格 + 接口索引表），不二次调LLM

链路2：code分析（source='code'，降级路径）
  _generate_by_rules 规则生成scenarios → _save_and_publish_table 确定性渲染
```

**统一产出（两条链路同格式）**：
- **测试点JSON**：`{id, endpoint, platform, scope, module, detail, priority, source, type}`，落盘 `generated_testpoints/{标题}_{时间戳}.json`
- **接口索引**：`{title, api_path, method, tag, reason}`，4类标签供测试人员 review，飞书文档末尾追加5列原生表格
- **TestBatch 结构**：`{platform, platform_label, batch_index, priority, shared_context:{framework,assertion_focus}, test_points[], depends_on[]}`，同端内按可配置 `max_per_batch` 切批（所有平台统一 batch_size=3），priority 取批内最高
- **飞书文档**：正文顶部含2行溯源链接（需求文档+需求分析，飞书原生超链接），概述段（总数/P0数/各端分布），按端 H2 分节 + 原生表格（每端序号从1开始），末尾接口索引表（5列：名称/路径/方法/标签/理由）；本地.md同步保存
- 降级：prd直提失败时降级规则生成 + 确定性表格渲染，产出不断

**关键方法**：
- `execute(input_data)`：结构化入口（支持 `raw_prd` / `structured_constraints` 可选参数）
- `process_query(query, context)`：自然语言入口（可独立调用）

### 4. TestGeneratorAgent（测试生成Agent）

**职责**：根据测试点批次生成 JSON 测试用例，按 platform 路由 3 类 prompt，LLM 截断时自动递归拆子批，生成后按端大类整合为飞书文档（每类1个）

**工作流程**：
```
输入：test_point_batch（含 platform + test_points + shared_context + requirement_context）
  ↓
安全断言：_MAX_POINTS_PER_BATCH=5，超过自动拆批
  ↓
按 batch.platform 查 _PROMPT_REGISTRY 路由对应 prompt 文件
  ↓ client_test.md（App/Web/H5/通用/E2E）
  ↓ admin_test.md（管理后台）
  ↓ backend_test.md（后端服务）
注入参数：platform_label / framework / assertion_focus / batch_index / test_points_list / requirement_context
  ↓
LLM 生成 JSON 测试用例（9字段：test_module/test_point/test_scenario/test_data/test_steps/expected_results/priority/status/remarks）
  ↓
截断检测 _is_truncated → 递归拆子批（binary split，最小粒度2点，最大深度4层）
  ↓
按端大类分组（客户端/App/Web/H5/通用/E2E→客户端组，backend→后端组，admin→后台组）
  ↓
cases_to_feishu_struct：按 test_module 分组 → H1模块 + H2测试点 + 6列表格（优先级/测试场景/测试步骤/预期结果/执行结果/备注）
  ↓
按 _PLATFORM_FOLDER_MAP 路由飞书文件夹 → create_doc_from_struct 上传
  ↓
文档标题：【{需求标题}】{group}测试用例；顶部3行溯源链接（需求文档/需求分析/测试点分析）
  ↓
输出：{feishu_docs: [{group, platform, feishu_url, case_count}], total_cases, batch_results}
```

**飞书测试用例文件夹路由**（每端大类1个文档）：
| platform | 飞书文件夹 | 环境变量 |
|---|---|---|
| app / web / h5 / common / e2e | 客户端测试用例文件夹 | `FEISHU_TEST_FOLDER_CLIENT` |
| backend | 后端测试用例文件夹 | `FEISHU_TEST_FOLDER_BACKEND` |
| admin | 后台测试用例文件夹 | `FEISHU_TEST_FOLDER_ADMIN` |

飞书上传失败不阻塞测试生成（返回空串，已提取的用例仍返回）。

**3 类 prompt 路由表**（batch_size 统一为 3）：
| platform | prompt 文件 | framework | 断言重点 |
|---|---|---|---|
| app | client_test.md | Appium | 页面元素/交互流程/视觉状态 |
| web | client_test.md | Playwright | 页面导航/元素定位/显式等待 |
| h5 | client_test.md | Playwright | WebView兼容/页面适配/交互 |
| common | client_test.md | Playwright/Appium | UI交互/状态反馈/兼容性 |
| e2e | client_test.md | Playwright+requests | 跨端流程/数据流转/状态同步 |
| backend | backend_test.md | requests+pytest | 接口返回码/数据字段/数据库状态 |
| admin | admin_test.md | Playwright | 页面功能/权限控制/表单校验 |

**异步生成 + 前端轮询**：
```
POST /api/generate_async {task_id: "REQ-20260904-xxx"}
  → 立即返回 {gen_task_id: "GEN-REQ-xxx-171846", status: "queued"}
  → 后台线程执行全部批次生成 + 飞书文档创建
  → _generation_tasks 内存 dict 存储状态

GET /api/generate_status?gen_task_id=GEN-REQ-xxx-171846
  → 返回 {status, progress, total, logs, result?, error?}
  → status: queued/running/completed/failed
  → 前端每5秒轮询，展示 x/y批 + 最近日志
```

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
Step 1: RequirementAnalyzer 分析代码/PRD/飞书链接 → 写入飞书 + 返回分析文与raw_content
  ↓
Step 2: 按metadata.source选链路：prd/feishu来源→source='prd'+透传raw_prd；代码→解析Markdown为结构化需求
  ↓
Step 3: TestPointGenerator 双链路产出测试点（四列表格 + JSON落盘）
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

#### 飞书链接直连需求分析（独立脚本）
```bash
# 一步完成：飞书文档拉取 → 需求分析 → 写飞书 + 本地落盘，全程分步耗时日志
python run_requirement.py https://your_company.feishu.cn/docx/xxxxx
# 也支持 /wiki/ 路径，自动解析token与文档类型、继承文档标题
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

**界面功能**：
- 需求分析 / 测试点生成 / 测试生成 三个标签页，支持飞书文档链接导入
- 底部实时日志面板（增量轮询，可折叠/暂停/清空），分析期间按钮置灰防重复提交
- 需求分析面板下方「历史记录」区，展示可点击的原始需求文档链接与生成的需求分析飞书文档链接

> 启动时使用 `use_reloader=False`，避免长耗时请求（需求分析约1-2分钟）因代码改动触发 reloader 重启而被中断。

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

# 模式C：prd直提（可选参数）
{
  "requirements": [{"description": "需求文本"}],
  "raw_prd": "原始PRD全文（缺省时用description）",
  "structured_constraints": "外部约束清单（不传则自动跑分支A提取）"
}
```

**4. 测试生成（异步，推荐）**
```http
POST /api/generate_async
Content-Type: application/json

{
  "task_id": "REQ-20260904-101235"
}
# → 立即返回 {gen_task_id, status:"queued"}
# → 后台线程逐批 execute_batch → 按端大类整合为飞书文档
# → 按 _PLATFORM_FOLDER_MAP 路由飞书文件夹（客户端/后端/后台各1个）
```

**5. 异步生成状态查询**
```http
GET /api/generate_status?gen_task_id=GEN-REQ-20260904-101235-171846
# 返回：{
#   gen_task_id, status: queued|running|completed|failed,
#   progress, total, created_at, started_at, finished_at,
#   logs: [{ts, text}]（最近20条）,
#   result?: {total_cases, feishu_docs: [{group, platform, feishu_url, case_count}], batch_results},
#   error?: string
# }
```

**6. 单独测试生成（同步，兼容）**
```http
POST /api/generate
Content-Type: application/json

# 模式A：task_id（推荐，从测试点JSON加载batches逐批生成）
{
  "task_id": "REQ-20260904-101235"
}
# → 从 history.json 查找测试点JSON → 加载batches → execute_batch逐批生成
# → 按platform路由prompt + 上传到对应端飞书文件夹
# → 返回 {generated_files, test_files(含feishu_url), total_batches}

# 模式B：自然语言（老路径）
{
  "query": "补充说明（可选）",
  "requirement_doc": "需求文档内容",
  "context": {
    "source_file": "demo/login.py",
    "function": "login"
  }
}
```

**7. 状态查询**
```http
GET /api/status
```

**8. 实时日志（增量轮询）**
```http
GET /api/logs?since=0
# 返回：{ logs: [{seq, text}], latest }
# 前端底部日志面板按 seq 增量拉取，since 传上次 latest，避免全量重传
```

**9. 需求分析历史记录**
```http
GET /api/history?limit=3&offset=0
# 返回：{ items: [{ created_at, source_doc_url, source_title, feishu_url, local_path, task_id }], total }
# 前端历史记录区展示可点击的「原始需求文档」与「需求分析飞书文档」链接 + 任务ID（点击复制）
# 分页加载：默认展示3条，点击「加载更多」每次追加5条，直到全部加载完
# 每次需求分析成功后自动追加到 generated_requirements/history.json（最新在前，最多100条）
```

**10. 飞书文档链接解析**
```http
POST /api/feishu/parse
Content-Type: application/json

{
  "doc_url": "https://your_company.feishu.cn/docx/xxxxx"
}

# 返回：success + content（文档内容，已清理控制字符）+ doc_type + doc_token + title
# 供前端飞书链接输入框预检与内容预览使用（支持 docx/wiki/sheets）
# 后端自动清理 ASCII 0-31 控制字符（保留 \n\r\t），避免前端 JSON.parse 失败
```

**11. 飞书机器人事件回调**
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

意图识别：关键词规则匹配（零延迟）→ LLM分类（兜底）

---

## 📝 示例演示

### 示例1：分析登录功能测试需求

**输入**：
```python
req_agent = RequirementAnalyzer(llm_client=api_key)
result = req_agent.process_query("分析demo/login.py的测试需求")
```

**输出**（新链路）：
```json
{
  "markdown": "需求分析：...",              // 结构化文本（h1-h3/段落/列表/表格/code）
  "local_path": "generated_requirements/xxx.md",
  "feishu_url": "https://xueqiu.feishu.cn/docx/xxx",
  "feishu_doc": "xxx",
  "raw_content": "原始需求文档清洗后全文",   // 供下游prd直提作主材料
  "metadata": {
    "source": "prd_document",                // prd_document/feishu_doc/代码
    "title": "AI搜索一期",
    "model": "qwen-plus"
  }
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
# Step 1: RequirementAnalyzer 分析代码/PRD/飞书链接 → struct直写飞书文档
# Step 2: 按metadata.source选链路（prd直提透传raw_prd / 代码解析结构化需求）
# Step 3: TestPointGenerator 双链路产出测试点（四列表格写飞书 + JSON落盘）
# Step 4: TestGeneratorAgent 生成可执行测试代码
#
# 输出：
# ✓ 需求分析完成，飞书文档已创建
# ✓ 测试点已写入飞书（code块四列表格）+ JSON落盘
# ✓ 流水线完成！共生成 N 个测试文件
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
FEISHU_OUTPUT_FOLDER=your_folder_token         # 需求分析文档目录
FEISHU_TESTPOINT_FOLDER=your_folder_token       # 测试点文档目录
FEISHU_TEST_FOLDER_CLIENT=your_folder_token     # 客户端测试用例目录
FEISHU_TEST_FOLDER_ADMIN=your_folder_token      # 后台测试用例目录
FEISHU_TEST_FOLDER_BACKEND=your_folder_token    # 后端测试用例目录
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
- **飞书文档集成 + 机器人交互**
- 需求分析、测试点、测试用例均自动写入飞书文档，支持在线协作评审
- **struct直写原生块**：业务JSON ↔ structured_doc模型 ↔ 飞书原生块，无Markdown解析损耗；h1-h3/段落/列表/表格/code 8种节点
- **markdown超链接解析**：`_parse_inline` 支持 `[text](url)` → 飞书原生 `text_element_style.link`，测试点文档溯源链接可点击
- **日志上下文标注**：飞书 API 调用日志区分 `[主文档]` 与 `[关联文档]`，关联文档失败自动降级为 WARNING（不影响主文档分析）
- **测试用例按端分类上传**：3 个飞书文件夹（客户端/后台/后端），由 `_PLATFORM_FOLDER_MAP` 按 platform 自动路由
- 表格对齐规则（CJK宽度）与表格宽度自适应（列数×平均列宽≤页面宽度才设width，防横向溢出）在Agent侧确定性生成
- 通过 `FEISHU_OUTPUT_FOLDER` / `FEISHU_TESTPOINT_FOLDER` / `FEISHU_TEST_FOLDER_*` 环境变量分别指定需求分析、测试点、测试用例目标文件夹
- 飞书域名通过 `FEISHU_DOMAIN` 环境变量配置（默认 `xueqiu.feishu.cn`）
- `access_token` 自动过期刷新（提前60秒续期，避免长时间运行后失效）
- 飞书机器人支持自然语言对话，自动识别意图调用对应Agent
- 异步处理消息（避免飞书 3 秒超时限制），`event_id` 去重防止重复处理

### 7. Prompt与代码分离
- 每个Agent目录下直接存放 `.md` 纯文本文件，整个文件即prompt指令
- `agents/_prompt_utils.py` 为共享加载器，负责读取md、校验占位符、注入参数
- 编辑prompt无需理解Python语法，不会误删代码
- 启动时自动校验占位符是否存在，缺失即报错

### 8. 主辅材料双输入设计（测试点prd直提）
- **主材料**：原始PRD全文，唯一事实来源，所有测试点必须回溯至PRD原文
- **辅助材料**：约束清单（分支A自动提取或外部注入），仅作防遗漏索引
- **门控指令**：辅助材料不作事实来源、无视"推荐默认值/置信度"等推测性内容、冲突以PRD为准
- 效果：降低幻觉，兼顾覆盖率与可解释性

### 9. YAPI 接口数据集成
- **链接提取**：文档清洗前从主文档+关联文档中提取 YAPI URL（正则匹配 `yapi.*\/project\/\d+\/interface\/api(\/\d+)?`），去重存入 metadata.yapi_urls
- **接口详情拉取**：通过内部接口 `https://ugcqams.snowballfinance.com/internal/getInterfaceData?interfaceYapiId=xxx` 获取接口完整定义
- **标准化映射层**：`_normalize_yapi_interface()` 将 YAPI 原始响应转为统一结构 `{api_path, method, title, params, response_schema, desc, change_type, related_requirement}`，后两个字段留空待 AI 分析
- **出参结构化摘要**：`_summarize_res_schema()` 将 response_schema（dict/str）转为字段名+类型+必填+描述(50字)摘要，替代800字符硬截断；整体YAPI截断6000→8000，单接口200-300字符可覆盖20+接口
- **AI 接口打标**：4 类标签（本次新增/本次修改/存量复用-建议回归/存量复用-无需测试），不丢弃任何接口，输出接口索引表供测试人员 review
- **异常降级**：404(已删除)/401,403(鉴权)/Timeout 分别记录 WARNING，部分失败跳过不阻塞，无接口数据时 AI 仅基于 PRD 正常工作

---

## 📊 项目统计

- **代码行数**：约 3800+ 行
- **核心模块**：5个Agent + 8个核心组件
- **Prompt 文件**：测试点 2 个（prd_to_testpoints / constraints_extract）+ 测试用例 3 类（client / admin / backend）
- **支持测试类型**：7 端（App/Web/H5/通用/E2E/后端/管理后台），3 类 prompt 路由
- **支持LLM提供商**：3种（百炼DashScope/DeepSeek/OpenAI）
- **交互方式**：4种（CLI/Web/飞书机器人/飞书链接独立脚本）
- **飞书集成**：需求分析 + 测试点 + 测试用例三类文档自动写入（测试用例按端分3个文件夹）+ 飞书机器人对话 + Web历史记录分页+任务ID
- **测试点产出**：按端分节表格（飞书原生表格 + 溯源链接）+ 接口索引表（4类标签review）+ 九字段JSON + TestBatch批次落盘，双链路统一
- **YAPI 集成**：主文档+关联文档 YAPI 链接提取 → 内部接口拉取详情 → 标准化映射 → AI 打标 → 接口索引表

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

**最后更新时间**：2026-09-04
