"""测试点生成Agent - 将需求转化为测试场景和测试点"""
import json
import os
import re
from datetime import datetime
from typing import Any
from agents.base_agent import BaseAgent
from agents._prompt_utils import build_prompt
from core.structured_doc import parse_struct_json, struct_to_markdown

_DIR = os.path.dirname(os.path.abspath(__file__))

# scenario字段 → 中文测试类型
_SCENARIO_TYPE_MAP = {'normal': '正向', 'edge_case': '边界', 'error_handling': '异常'}
# priority → P级映射（确定性降级用）
_PRIORITY_P_MAP = {'high': 'P0', 'medium': 'P1', 'low': 'P2'}


def _display_width(s: str) -> int:
    """显示宽度：CJK/全角字符记2列，其余记1列（等宽字体对齐依据）"""
    w = 0
    for ch in s:
        w += 2 if '\u4e00' <= ch <= '\u9fff' or ch in '（）【】“”，。；：、！？—…％' or '\uff00' <= ch <= '\uffef' or '\u3000' <= ch <= '\u303f' else 1
    return w


def align_plain_table(headers: list, rows: list, gap: int = 2) -> str:
    """二维数据 → 纯文本对齐表格（按显示宽度补空格，无Markdown符号）"""
    all_rows = [headers] + rows
    n_col = len(headers)
    widths = []
    for c in range(n_col):
        widths.append(max(_display_width(str(r[c])) if c < len(r) else 0 for r in all_rows))
    
    def pad(cell, width):
        cell = str(cell)
        return cell + ' ' * (width - _display_width(cell))
    
    lines = []
    for i, r in enumerate(all_rows):
        cells = [pad(r[c] if c < len(r) else '', widths[c]) for c in range(n_col)]
        lines.append((' ' * gap).join(cells).rstrip())
        if i == 0:
            lines.append((' ' * gap).join('-' * widths[c] for c in range(n_col)).rstrip())
    return '\n'.join(lines)


class TestPointGenerator(BaseAgent):
    """测试点生成Agent：接收需求分析结果，输出结构化测试场景，
    并生成表格形式的测试点清单（本地.md + 飞书文档）"""
    
    def __init__(self, llm_client=None, feishu_client=None, output_dir='generated_testpoints'):
        super().__init__(name="TestPointGenerator", llm_client=llm_client)
        self.feishu_client = feishu_client
        self.output_dir = output_dir
        self.feishu_folder = os.getenv('FEISHU_TESTPOINT_FOLDER') or os.getenv('FEISHU_OUTPUT_FOLDER', '')
        os.makedirs(output_dir, exist_ok=True)
    
    def execute(self, input_data: dict) -> dict:
        """
        根据需求生成测试场景
        :param input_data: {'requirements': list, 'test_type': str, 'source': str}
        :return: {'scenarios': list, 'metadata': dict}
        """
        if not self.validate_input(input_data):
            raise ValueError("Invalid input: requirements list required")
        
        requirements = input_data['requirements']
        test_type = input_data.get('test_type', 'web')
        source = input_data.get('source', 'code')  # 'code' or 'prd'
        doc_title = input_data.get('title') or '测试点清单'
        
        # 根据来源选择链路
        json_path = None
        test_points = []
        if source == 'prd':
            # prd直提链路：主材料=原始PRD全文，辅助材料=约束清单（防遗漏索引）
            prd_text = input_data.get('raw_prd') or ''
            if not prd_text and requirements:
                # 无原始文本时退回requirements序列化（兼容旧入口）
                prd_text = json.dumps(requirements, ensure_ascii=False)
            # structured_constraints: None=自动跑分支A提取；显式传入则直接使用
            structured_constraints = input_data.get('structured_constraints')
            test_points = self._extract_testpoints_from_prd(prd_text, structured_constraints)
            batches = []
            if test_points:
                local_path, feishu_url, json_path, batches = self._publish_points(test_points, doc_title)
                scenarios = self._points_to_scenarios(test_points)  # 下游兼容（旧路径）
            else:
                print("  [测试点] prd直提失败, 降级规则生成 + 阶段2表格链路")
                scenarios = self._generate_by_rules(requirements)
                local_path, feishu_url, json_path, test_points = self._save_and_publish_table(scenarios, doc_title)
        else:
            batches = []
            scenarios = self._generate_from_code_requirements(requirements, test_type)
            # 表格产出：本地.md + 飞书文档（列结构由testpoints_table.md指定）
            # 同时提取测试点JSON落盘，供下游用例生成（分批送入大模型）使用
            local_path, feishu_url, json_path, test_points = self._save_and_publish_table(scenarios, doc_title)

        self.state = {
            'scenario_count': len(scenarios),
            'test_point_count': len(test_points),
            'batch_count': len(batches),
            'source': source,
            'test_type': test_type,
            'local_path': local_path,
            'json_path': json_path,
            'feishu_url': feishu_url
        }

        return {
            'scenarios': scenarios,
            'test_points': test_points,
            'batches': batches,  # 新：子端分组+分批，供下游按端路由prompt
            'test_points_json_path': json_path,
            'local_path': local_path,
            'feishu_url': feishu_url,
            'metadata': self.state
        }
    
    def validate_input(self, input_data: Any) -> bool:
        if not isinstance(input_data, dict):
            return False
        return 'requirements' in input_data and isinstance(input_data['requirements'], list)
    
    def process_query(self, query: str, context: dict = None) -> dict:
        """自然语言处理入口
        :param query: 用户查询，如“根据login功能生成测试场景”
        :param context: 上下文信息（可选），如{'requirements': [...], 'test_type': 'web'}
        :return: {'scenarios': list, 'metadata': dict}
        """
        context = context or {}
        
        # 如果context中已提供requirements，直接使用
        requirements = context.get('requirements')
        test_type = context.get('test_type', 'web')
        
        if not requirements:
            # 尝试从自然语言中提取需求（LLM或规则）
            requirements = self._parse_requirements_from_query(query)
        
        return self.execute({
            'requirements': requirements,
            'test_type': test_type,
            'source': context.get('source', 'code'),
            'title': context.get('title')
        })
    
    def _parse_requirements_from_query(self, query: str) -> list:
        """从自然语言中提取需求列表"""
        # 尝试LLM解析
        if self.llm:
            prompt = f"""你是需求解析助手。从用户查询中提取功能需求，返回JSON格式。

用户查询: {query}

返回格式：{{"requirements": [{{"function": "函数名", "complexity": "medium", "test_points": ["测试点"]}}]}}
如无法提取具体函数，返回：{{"requirements": [{{"function": "all", "name": "用户需求", "complexity": "medium", "test_points": ["功能逻辑"], "description": "原始查询内容"}}]}}
只返回JSON。"""
            try:
                response = self.llm.generate(prompt)
                cleaned = re.sub(r'```(?:json)?\s*', '', response)
                cleaned = re.sub(r'```', '', cleaned)
                json_match = re.search(r'\{.*\}', cleaned, re.DOTALL)
                if json_match:
                    result = json.loads(json_match.group(0))
                    reqs = result.get('requirements', [])
                    if reqs:
                        return reqs
            except Exception as e:
                print(f"⚠ [TestPointGenerator] LLM解析查询失败: {e}")
        
        # 降级：提取英文函数名作为需求
        func_names = re.findall(r'\b([a-zA-Z_]\w*)\b', query)
        if func_names:
            return [{'function': name, 'complexity': 'medium', 'test_points': ['功能逻辑']} for name in func_names[:3]]
        
        # 最低降级：整段查询作为单条需求
        return [{'function': 'all', 'name': '用户需求', 'complexity': 'medium', 'test_points': ['功能逻辑'], 'description': query[:500]}]
    
    def _generate_from_code_requirements(self, requirements: list, test_type: str) -> list:
        """从代码分析的需求生成测试场景"""
        if self.llm and requirements:
            scenarios = self._generate_with_llm(requirements, test_type)
            if scenarios:
                return scenarios
        
        # 降级方案：规则生成
        return self._generate_by_rules(requirements)
    
    # ---------- prd直提链路（端分组JSON → 子端分组 + 同端内分批 TestBatch） ----------
    
    # platform → (label, max_per_batch, prompt文件, framework, assertion_focus)
    # 端维度决定下游 TestGenerator 用哪套 prompt/框架；批次维度控制单次生成数量
    _PLATFORM_CONFIG = {
        'app':     ('客户端-App',  8,  'app_test.md',     'Appium',              '页面元素/交互流程/视觉状态'),
        'web':     ('客户端-Web',  8,  'web_test.md',     'Playwright',          '页面导航/元素定位/显式等待'),
        'h5':      ('客户端-H5',   8,  'h5_test.md',      'Playwright',          'WebView兼容/页面适配/交互'),
        'common':  ('客户端-通用', 8,  'common_test.md',  'Playwright/Appium',   'UI交互/状态反馈/兼容性'),
        'backend': ('后端服务',    12, 'backend_test.md', 'requests+pytest',     '接口返回码/数据字段/数据库状态'),
        'admin':   ('管理后台',    10, 'admin_test.md',   'Playwright',          '页面功能/权限控制/表单校验'),
        'e2e':     ('端到端集成',   6,  'e2e_test.md',     'Playwright+requests', '跨端流程/数据流转/状态同步'),
    }
    # 端分组JSON路径 → platform（扩展现状：新增 backend/admin/e2e 提取）
    _GROUP_PLATFORM_MAP = [
        (('client', 'app'), 'app'),
        (('client', 'web'), 'web'),
        (('client', 'h5'),  'h5'),
        (('client', 'common'), 'common'),
        (('backend',),          'backend'),
        (('operation_backend',), 'admin'),   # prompt键保留 operation_backend，代码层映射 admin
        (('e2e',),              'e2e'),
    ]
    # 兼容别名（旧名指向新语义：元素为 (path, platform)）
    _GROUP_ENDPOINT_MAP = _GROUP_PLATFORM_MAP
    
    # 合法测试点类型（供下游分批）
    _VALID_TYPES = {'normal', 'edge_case', 'error_handling'}
    
    def _extract_constraints(self, prd_text: str) -> str:
        """分支A：从原始PRD提取约束清单（防遗漏索引），失败返回''"""
        try:
            prompt = build_prompt(_DIR, 'constraints_extract.md',
                prd_requirements=prd_text[:12000])
            response = self.llm.generate(prompt, max_tokens=4000)
            constraints = (response or '').strip()
            if constraints:
                print(f"  [约束清单] 分支A提取成功: {len(constraints)}字符")
            return constraints
        except Exception as e:
            print(f"  [约束清单] 分支A提取失败(跳过辅助材料): {type(e).__name__}: {str(e)[:100]}")
            return ''
    
    def _extract_testpoints_from_prd(self, prd_text: str, structured_constraints: str = None) -> list:
        """按prd_to_testpoints.md直提四列测试点：主材料=原始PRD，辅助材料=约束清单（门控使用）
        :param structured_constraints: None=自动跑分支A；''=无辅助材料；其他=直接使用
        """
        if not (self.llm and prd_text):
            return []
        try:
            if structured_constraints is None:
                structured_constraints = self._extract_constraints(prd_text)
            prompt = build_prompt(_DIR, 'prd_to_testpoints.md',
                prd_requirements=prd_text[:12000],
                structured_constraints=(structured_constraints or '（无辅助材料）')[:4000])
            response = self.llm.generate(prompt, max_tokens=16000)
            grouped = self._parse_grouped_json(response)
            if not isinstance(grouped, dict):
                return []
            points = []
            for path, platform in self._GROUP_PLATFORM_MAP:
                node = grouped
                for k in path:
                    node = node.get(k) if isinstance(node, dict) else None
                if not isinstance(node, list):
                    continue
                label = self._PLATFORM_CONFIG[platform][0]  # 涉及端中文标签
                for item in node:
                    if isinstance(item, dict) and str(item.get('detail', '')).strip():
                        t = str(item.get('type', 'normal')).strip().lower()
                        points.append({
                            'id': '',
                            'endpoint': label,    # 涉及端（中文，飞书表格展示用）
                            'platform': platform,  # 英文键，供下游 TestGenerator 路由 prompt
                            'detail': re.sub(r'[\r\n]+', ' ', str(item['detail'])).strip()[:120],
                            'priority': str(item.get('priority', 'P1')).strip().upper(),
                            'source': 'prd',  # 来源标识，供下游追溯
                            'type': t if t in self._VALID_TYPES else 'normal'
                        })
            # 统一重新编号（保证01连续递增，防LLM漏号/重复）
            for i, p in enumerate(points, 1):
                p['id'] = f'{i:02d}'
            if points:
                print(f"✓ [TestPointGenerator] prd直提 {len(points)} 个测试点")
            return points
        except Exception as e:
            print(f"⚠ [TestPointGenerator] prd直提异常: {type(e).__name__}: {str(e)[:100]}")
            return []
    
    @staticmethod
    def _parse_grouped_json(text: str):
        """解析端分组JSON对象（去代码块标记 + 截取花括号区间 + 尾逗号修复）"""
        cleaned = re.sub(r'```(?:json)?\s*', '', text or '')
        cleaned = re.sub(r'```', '', cleaned)
        m = re.search(r'\{.*\}', cleaned, re.DOTALL)
        if not m:
            return None
        s = m.group(0)
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            fixed = re.sub(r',\s*([}\]])', r'\1', s)
            try:
                return json.loads(fixed)
            except json.JSONDecodeError as e:
                print(f"⚠ [TestPointGenerator] 端分组JSON解析失败: {str(e)[:80]}")
                return None
    
    def _split_into_batches(self, test_points: list) -> list:
        """按 platform 分组 + 同端内按 max_per_batch 切批 → List[TestBatch]
        每批注入该端 shared_context（静态），batch_index 在端内连续，priority 取批内最高
        """
        by_platform = {}
        for p in test_points:
            by_platform.setdefault(p.get('platform', 'common'), []).append(p)
        priority_rank = {'P0': 0, 'P1': 1, 'P2': 2}
        batches = []
        for platform, cfg in self._PLATFORM_CONFIG.items():
            pts = by_platform.get(platform, [])
            if not pts:
                continue
            label, max_per_batch, _prompt_file, framework, assertion_focus = cfg
            pts_sorted = sorted(pts, key=lambda x: priority_rank.get(x.get('priority', 'P1'), 1))
            for idx in range(0, len(pts_sorted), max_per_batch):
                chunk = pts_sorted[idx:idx + max_per_batch]
                batch_priority = min(
                    (p.get('priority', 'P1') for p in chunk),
                    key=lambda pr: priority_rank.get(pr, 1))
                batches.append({
                    'platform': platform,
                    'platform_label': label,
                    'batch_index': idx // max_per_batch + 1,
                    'priority': batch_priority,
                    'shared_context': {
                        'framework': framework,
                        'assertion_focus': assertion_focus,
                    },
                    'test_points': chunk,
                    'depends_on': [],  # 预留：批次间依赖，首期留空
                })
        return batches

    def _publish_points(self, test_points: list, title: str) -> tuple:
        """直提链路发布：子端分组 + 同端内分批 → 本地.md + JSON落盘(含batches) + 飞书(按端分节原生表格)
        :return: (local_path, feishu_url, json_path, batches)
        """
        batches = self._split_into_batches(test_points)
        n_p0 = sum(1 for p in test_points if p['priority'] == 'P0')
        # 飞书 struct：按端分节，每端一个 h2 + 原生飞书表格
        HEADERS = ['序号', '测试点详情', '优先级', '涉及端']
        struct_nodes = [
            {'type': 'h1', 'text': f'{title} - 测试点清单'},
            {'type': 'paragraph', 'text': f'共{len(test_points)}个测试点（{len(batches)}批），其中P0 {n_p0}个。'},
        ]
        for b in batches:
            rows = [[i+1, p['detail'], p['priority'], p.get('endpoint', b['platform_label'])]
                    for i, p in enumerate(b['test_points'])]
            struct_nodes.append({
                'type': 'h2',
                'text': f"{b['platform_label']} · 第{b['batch_index']}批（{len(b['test_points'])}个，最高优先级{b['priority']}）"
            })
            struct_nodes.append({'type': 'table', 'headers': HEADERS, 'rows': rows})
        md = struct_to_markdown(struct_nodes)

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        safe_title = re.sub(r'[^\w\u4e00-\u9fff-]', '_', title)[:50]
        local_path = os.path.join(self.output_dir, f"{safe_title}_{timestamp}.md")
        with open(local_path, 'w', encoding='utf-8') as f:
            f.write(md)
        print(f"✓ [TestPointGenerator] 测试点表格本地保存: {local_path}")

        json_path = os.path.join(self.output_dir, f"{safe_title}_{timestamp}.json")
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump({'title': title, 'total': len(test_points), 'batches': batches,
                       'test_points': test_points},  # 扁平列表保留，向后兼容
                      f, ensure_ascii=False, indent=2)
        print(f"✓ [TestPointGenerator] 测试点JSON保存({len(test_points)}条/{len(batches)}批): {json_path}")

        feishu_url = None
        if self.feishu_client and self.feishu_folder:
            try:
                result = self.feishu_client.create_doc_from_struct(
                    title=f"[测试点] {title}",
                    folder_token=self.feishu_folder,
                    struct_blocks=struct_nodes
                )
                feishu_url = result['url']
                print(f"  [测试点] 飞书文档创建成功: {feishu_url}")
            except Exception as e:
                print(f"  [测试点] 飞书文档创建失败: {type(e).__name__}: {str(e)[:200]}")

        return local_path, feishu_url, json_path, batches
    
    @staticmethod
    def _points_to_scenarios(test_points: list) -> list:
        """测试点 → 兼容下游orchestrator Step3的scenario结构"""
        p_map = {'P0': 'high', 'P1': 'medium', 'P2': 'low'}
        return [{
            'function': p.get('platform', p['endpoint']),  # 用英文 platform 键作 function（适合文件名）
            'platform': p.get('platform', 'common'),       # 透传，供下游路由
            'scenario': p.get('type', 'normal'),
            'description': p['detail'],
            'priority': p_map.get(p['priority'], 'medium'),
            'test_points': [p['detail']]
        } for p in test_points]
    
    def _generate_with_llm(self, requirements: list, test_type: str) -> list:
        """用LLM生成测试场景"""
        prompt = build_prompt(_DIR, 'generate_scenarios.md',
            test_type=test_type,
            requirements=json.dumps(requirements, ensure_ascii=False)[:3000])
        return self._call_llm(prompt)
    
    def _call_llm(self, prompt: str) -> list:
        """调用LLM并解析JSON响应"""
        max_retries = 2
        for attempt in range(max_retries):
            try:
                response = self.llm.generate(prompt)
                
                # 清理代码块标记
                cleaned = re.sub(r'```(?:json)?\s*', '', response)
                cleaned = re.sub(r'```', '', cleaned)
                
                json_match = re.search(r'\{.*\}', cleaned, re.DOTALL)
                if not json_match:
                    print(f"⚠ [TestPointGenerator] 第{attempt+1}次: 未找到JSON")
                    continue
                
                json_str = json_match.group(0)
                
                try:
                    result = json.loads(json_str)
                except json.JSONDecodeError as e:
                    print(f"⚠ [TestPointGenerator] JSON解析失败, 尝试修复: {str(e)[:80]}")
                    fixed = re.sub(r',\s*([}\]])', r'\1', json_str)
                    fixed = re.sub(r"'([^']*)'", r'"\1"', fixed)
                    fixed = re.sub(r':\s*,', ': null,', fixed)
                    fixed = re.sub(r':\s*([}\]])', r': null\1', fixed)
                    result = json.loads(fixed)
                
                scenarios = result.get('scenarios', [])
                if isinstance(scenarios, list) and len(scenarios) > 0:
                    print(f"✓ [TestPointGenerator] LLM生成 {len(scenarios)} 个测试场景")
                    return scenarios
                
            except Exception as e:
                print(f"⚠ [TestPointGenerator] 第{attempt+1}次失败: {type(e).__name__}: {str(e)[:100]}")
        
        return []
    
    def _generate_by_rules(self, requirements: list) -> list:
        """规则降级方案：每个需求生成正常/边界/异常三类场景"""
        scenarios = []
        
        for req in requirements:
            func_name = req.get('function') or req.get('name', 'unknown')
            complexity = req.get('complexity', 'low')
            test_points = req.get('test_points', ['功能逻辑'])
            
            # 正常场景
            scenarios.append({
                'function': func_name,
                'scenario': 'normal',
                'description': f"测试{func_name}正常流程",
                'precondition': '系统正常运行，输入数据合法',
                'steps': f'调用{func_name}，传入合法参数',
                'expected': '功能正常执行，返回预期结果',
                'priority': 'high',
                'test_points': test_points
            })
            
            # 边界场景（中高复杂度）
            if complexity in ['medium', 'high']:
                scenarios.append({
                    'function': func_name,
                    'scenario': 'edge_case',
                    'description': f"测试{func_name}边界条件",
                    'precondition': '输入处于边界值',
                    'steps': f'传入边界值参数调用{func_name}',
                    'expected': '系统正确处理边界情况',
                    'priority': 'medium',
                    'test_points': ['边界值', '异常输入']
                })
            
            # 异常场景
            scenarios.append({
                'function': func_name,
                'scenario': 'error_handling',
                'description': f"测试{func_name}异常处理",
                'precondition': '系统异常状态或非法输入',
                'steps': f'传入非法参数或模拟异常调用{func_name}',
                'expected': '系统给出明确错误提示，不崩溃',
                'priority': 'high',
                'test_points': ['错误恢复', '降级处理']
            })
        
        return scenarios
    
    # ---------- 表格产出（本地.md + 飞书文档） ----------
    
    def _save_and_publish_table(self, scenarios: list, title: str) -> tuple:
        """生成测试点表格文档：LLM按testpoints_table.md输出业务JSON → struct直写飞书；
        JSON失败时降级为确定性表格渲染（保证一定有产出）。
        同时提取四列测试点JSON落盘，供下游用例生成消费
        :return: (local_path, feishu_url, json_path, test_points)
        """
        if not scenarios:
            print("  [测试点表格] 无场景数据, 跳过表格产出")
            return None, None, None, []
        
        # 1. LLM生成业务JSON（列结构由prompt指定）→ 解析失败降级确定性渲染
        struct_nodes = None
        if self.llm:
            struct_nodes = self._gen_table_struct(scenarios)
        
        if struct_nodes:
            # 先从table节点提取测试点JSON（供下游），再转对齐文本（顺序不可颠倒）
            test_points = self._extract_test_points(struct_nodes)
            md = struct_to_markdown(struct_nodes)
            mode = 'LLM表格(原生飞书表格)'
        else:
            print("  [测试点表格] LLM表格链路失败/未启用, 降级确定性渲染")
            struct_nodes = self._render_scenarios_struct(scenarios, title)
            test_points = self._fallback_test_points(scenarios)
            md = struct_to_markdown(struct_nodes)
            mode = '确定性渲染(纯文本对齐)'
        
        # 2. 保存本地.md
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        safe_title = re.sub(r'[^\w\u4e00-\u9fff-]', '_', title)[:50]
        local_path = os.path.join(self.output_dir, f"{safe_title}_{timestamp}.md")
        with open(local_path, 'w', encoding='utf-8') as f:
            if not md.lstrip().startswith('#'):
                f.write(f"# {title}\n\n")
            f.write(md)
        print(f"✓ [TestPointGenerator] 测试点表格本地保存: {local_path}")
        
        # 2.5 测试点JSON落盘（下游用例生成消费：分批送入大模型B）
        json_path = None
        if test_points:
            json_path = os.path.join(self.output_dir, f"{safe_title}_{timestamp}.json")
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump({'title': title, 'total': len(test_points), 'test_points': test_points},
                          f, ensure_ascii=False, indent=2)
            print(f"✓ [TestPointGenerator] 测试点JSON保存({len(test_points)}条): {json_path}")
        
        # 3. 飞书文档（可选）：统一走struct直写（table已转为code对齐块）
        feishu_url = None
        if self.feishu_client and self.feishu_folder:
            print(f"  [测试点表格] 创建飞书文档: 模式={mode}")
            try:
                result = self.feishu_client.create_doc_from_struct(
                    title=f"[测试点] {title}",
                    folder_token=self.feishu_folder,
                    struct_blocks=struct_nodes
                )
                feishu_url = result['url']
                print(f"  [测试点表格] 飞书文档创建成功: {feishu_url}")
            except Exception as e:
                print(f"  [测试点表格] 飞书文档创建失败: {type(e).__name__}: {str(e)[:200]}")
        
        return local_path, feishu_url, json_path, test_points
    
    @staticmethod
    def _extract_test_points(struct_nodes: list) -> list:
        """从struct表格节点提取四列测试点JSON（合并多个拆分表格）
        按表头名定位列，容错：表头不匹配时按四列顺序取
        """
        key_map = {'测试点ID': 'id', '编号': 'id', '涉及端': 'endpoint',
                   '测试点详情': 'detail', '优先级': 'priority'}
        points = []
        for n in struct_nodes:
            if n['type'] != 'table':
                continue
            headers = n.get('headers', [])
            idx = {key_map.get(h, f'col_{i}'): i for i, h in enumerate(headers)}
            for row in n.get('rows', []):
                def cell(key, fallback_i):
                    i = idx.get(key, fallback_i)
                    return row[i].strip() if i < len(row) else ''
                points.append({
                    'id': cell('id', 0),
                    'endpoint': cell('endpoint', 1),
                    'detail': cell('detail', 2),
                    'priority': cell('priority', 3),
                    'source': 'code',   # 阶段2表格无此列，代码补齐供下游追溯
                    'type': 'normal'    # 表格规范不含类型列，默认值
                })
        return points
    
    @staticmethod
    def _fallback_test_points(scenarios: list) -> list:
        """降级路径：从scenarios直接构造四列测试点JSON（与_render_scenarios_struct同源）"""
        def flat(s):
            return re.sub(r'[\r\n]+', ' ', str(s)).strip()[:80]
        points = []
        for i, s in enumerate(scenarios, 1):
            module = flat(s.get('function') or s.get('name', ''))
            desc = flat(s.get('description', ''))
            points.append({
                'id': f'{i:02d}',
                'endpoint': '客户端',
                'detail': f'{module} - {desc}' if module else desc,
                'priority': _PRIORITY_P_MAP.get(s.get('priority', ''), flat(s.get('priority', ''))),
                'source': 'code',
                'type': s.get('scenario', 'normal') or 'normal'
            })
        return points
    
    def _gen_table_struct(self, scenarios: list):
        """调LLM按testpoints_table.md生成业务JSON节点列表，失败返回None"""
        try:
            prompt = build_prompt(_DIR, 'testpoints_table.md',
                scenarios_json=json.dumps(scenarios, ensure_ascii=False)[:6000])
            response = self.llm.generate(prompt, max_tokens=16000)
            nodes = parse_struct_json(response)
            if nodes:
                n_table = sum(1 for n in nodes if n['type'] == 'table')
                print(f"  [测试点表格] LLM表格生成成功: {len(nodes)}个节点, {n_table}个表格")
            return nodes
        except Exception as e:
            print(f"  [测试点表格] LLM表格生成异常: {type(e).__name__}: {str(e)[:100]}")
            return None
    
    @staticmethod
    def _tables_to_aligned_code(struct_nodes: list) -> list:
        """把struct中的table节点转为纯文本对齐表格(code节点)，
        规整二维表用对齐文本写入飞书正文比Markdown/原生表格更稳妥"""
        out = []
        for n in struct_nodes:
            if n['type'] == 'table':
                out.append({'type': 'code',
                            'text': align_plain_table(n['headers'], n['rows'])})
            else:
                out.append(n)
        return out
    
    @staticmethod
    def _render_scenarios_struct(scenarios: list, title: str) -> list:
        """确定性降级：scenarios直接渲染为四列对齐表格struct节点（不依赖LLM）
        列: 编号 | 涉及端 | 测试点详情 | 优先级
        """
        def flat(s):
            return re.sub(r'[\r\n]+', ' ', str(s)).strip()[:80]
        
        rows = []
        for i, s in enumerate(scenarios, 1):
            module = flat(s.get('function') or s.get('name', ''))
            desc = flat(s.get('description', ''))
            rows.append([
                f'{i:02d}',
                '客户端',
                f'{module} - {desc}' if module else desc,
                _PRIORITY_P_MAP.get(s.get('priority', ''), flat(s.get('priority', '')))
            ])
        
        n_high = sum(1 for s in scenarios if s.get('priority') == 'high')
        return [
            {'type': 'h1', 'text': '测试点清单'},
            {'type': 'paragraph', 'text': f'共{len(scenarios)}个测试点，其中P0（高优先级）{n_high}个。'},
            {'type': 'table', 'headers': ['序号', '测试点详情', '优先级', '涉及端'],
             'rows': [[i+1, f'{flat(s.get("function") or s.get("name", ""))} - {flat(s.get("description", ""))}' if flat(s.get("function") or s.get("name", "")) else flat(s.get("description", "")),
                       _PRIORITY_P_MAP.get(s.get('priority', ''), flat(s.get('priority', ''))),
                       '客户端'] for i, s in enumerate(scenarios)]}
        ]
