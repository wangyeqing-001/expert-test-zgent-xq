"""测试生成Agent - 根据需求生成测试用例（JSON 输出，整合写入飞书文档）"""
import os
import re
import json
import time
import logging
from typing import Any
from datetime import datetime
from agents.base_agent import BaseAgent
from agents._prompt_utils import build_prompt
from core.llm_client import LLMClient
from core.tools import create_default_tools

logger = logging.getLogger(__name__)

_DIR = os.path.dirname(os.path.abspath(__file__))

# platform → 该端 prompt 文件（合并为 3 类：client/admin/backend）
# App/Web/H5/Common/E2E 统一走 client_test.md，prompt 内按 framework/assertion_focus 参数自动适配
_PROMPT_REGISTRY = {
    'app': 'client_test.md', 'web': 'client_test.md', 'h5': 'client_test.md',
    'common': 'client_test.md', 'e2e': 'client_test.md',
    'backend': 'backend_test.md',
    'admin': 'admin_test.md',
}

# platform → 飞书测试用例文件夹 token（按端分目录存放）
# 客户端: EmxffEI8elYwmgdqxbRcn1djnRg | 后台: SJNOfTJFclehy5dT8tzcVoKxnMe | 后端: EkzxfodGglVSrRdM9m9cQW4vnVd
_PLATFORM_FOLDER_MAP = {
    'app': 'EmxffEI8elYwmgdqxbRcn1djnRg', 'web': 'EmxffEI8elYwmgdqxbRcn1djnRg',
    'h5': 'EmxffEI8elYwmgdqxbRcn1djnRg', 'common': 'EmxffEI8elYwmgdqxbRcn1djnRg',
    'e2e': 'EmxffEI8elYwmgdqxbRcn1djnRg',
    'backend': 'EkzxfodGglVSrRdM9m9cQW4vnVd',
    'admin': 'SJNOfTJFclehy5dT8tzcVoKxnMe',
}

# platform → 大类标签（用于飞书文档分组）
_PLATFORM_GROUP_LABEL = {
    'app': '客户端', 'web': '客户端', 'h5': '客户端', 'common': '客户端', 'e2e': '客户端',
    'backend': '后端',
    'admin': '管理后台',
}


class TestGeneratorAgent(BaseAgent):
    """测试生成Agent：根据需求文档生成测试代码"""
    
    def __init__(self, api_key=None, base_url=None, test_type='web'):
        llm = LLMClient(api_key, base_url) if api_key != "mock" else None
        super().__init__(name="TestGeneratorAgent", llm_client=llm)
        self.output_dir = "generated_tests"
        self.test_type = test_type
        os.makedirs(self.output_dir, exist_ok=True)
        
        # 注册工具
        self.tools = create_default_tools()
        self._register_custom_tools()

    def execute(self, input_data: dict) -> dict:
        """
        执行测试生成任务（简化流程，完整流程用run()）
        旧链路兼容：生成 Python 代码并保存 .py 文件。
        新链路（execute_batch）输出 JSON 测试用例，不走此方法。
        :param input_data: {'scenario': dict, 'source_file': str}
        :return: {'test_case': dict, 'file_path': str}
        """
        if not self.validate_input(input_data):
            raise ValueError("Invalid input: scenario and source_file required")

        scenario = input_data['scenario']
        source_file = input_data['source_file']

        # 记录到工作记忆
        self.memory.add_working({'scenario': scenario, 'file': source_file})

        # 构建prompt
        prompt = self._build_prompt(scenario)

        # 调用LLM生成
        if self.llm:
            response = self.llm.generate(prompt)
        else:
            response = self._get_template(scenario['function'])

        # 旧链路：解析 Python 代码块（execute_batch 走 JSON 解析，不走这里）
        test_code = self._parse_code_response(response)

        # 构建测试结果
        test_case = {
            'function_name': scenario['function'],
            'scenario': scenario['scenario'],
            'test_code': test_code,
            'timestamp': datetime.now(),
            'priority': scenario.get('priority', 'medium')
        }
        
        # 反思评估
        evaluation = self.reflector.evaluate(test_case)
        
        # 保存经验
        self.memory.save_experience({
            'task_type': 'test_generation',
            'success': evaluation['passed'],
            'timestamp': str(datetime.now()),
            'details': evaluation.get('issues', [])
        })
        
        # 保存文件
        file_path = self.save_test_case(test_case, source_file)
        
        return {'test_case': test_case, 'file_path': file_path}

    def validate_input(self, input_data: Any) -> bool:
        """验证输入数据"""
        if not isinstance(input_data, dict):
            return False
        return 'scenario' in input_data and 'source_file' in input_data
    
    def process_query(self, query: str, context: dict = None) -> dict:
        """
        自然语言处理入口
        :param query: 用户查询，如“为login函数生成Web测试用例”或“为get_user_info生成API异常测试”
        :param context: 上下文信息（可选），如{'source_file': 'auth.py', 'scenarios': [...]}
        :return: {'test_case': dict, 'file_path': str}
        """
        context = context or {}
        # 1. 解析query提取测试需求
        parsed = self._parse_query(query)
        
        # 2. 构建场景对象
        scenario = {
            'function': parsed.get('function', context.get('function', 'unknown')),
            'scenario': parsed.get('scenario_type', 'normal'),
            'description': parsed.get('description', query),
            'priority': parsed.get('priority', 'medium'),
            'test_points': parsed.get('test_points', [])
        }
        
        # 3. 确定源文件
        source_file = parsed.get('source_file') or context.get('source_file')
        if not source_file:
            raise ValueError("未指定源文件，请在query中说明或在context中提供")
        
        # 4. 调用execute执行代码生成
        return self.execute({
            'scenario': scenario,
            'source_file': source_file
        })
    
    def _parse_query(self, query: str) -> dict:
        """解析自然语言查询，提取测试参数"""
        import re
        
        result = {
            'function': None,
            'scenario_type': 'normal',
            'description': query,
            'priority': 'medium',
            'test_points': [],
            'source_file': None
        }
        
        # 尝试用LLM解析（优先）
        if self.llm:
            prompt = f"""你是测试参数提取助手。从用户查询中提取测试生成参数。

用户查询: {query}

要求：
1. 提取函数名（如"login函数"中的"login"）
2. 识别场景类型：normal/edge_case/error_handling（默认normal）
3. 识别优先级：high/medium/low（默认medium）
4. 提取测试点列表（如"异常处理、超时重试"→["异常处理", "超时重试"]）
5. 提取源文件名（如果有）
6. 返回JSON格式：{{"function": "", "scenario_type": "normal", "priority": "medium", "test_points": [], "source_file": ""}}

只返回JSON，无解释。"""
            
            try:
                response = self.llm.generate(prompt)
                import json
                parsed = json.loads(response)
                result.update({k: v for k, v in parsed.items() if v})
                return result
            except Exception as e:
                print(f"LLM解析失败，使用规则匹配: {e}")
        
        # 降级方案：规则匹配
        # 提取函数名（匹配"xxx函数"或"function xxx"）
        func_pattern = r'([a-zA-Z_]\w*)函数|function\s+([a-zA-Z_]\w*)'
        func_match = re.search(func_pattern, query)
        if func_match:
            result['function'] = func_match.group(1) or func_match.group(2)
        
        # 增强：如果未匹配到，尝试提取纯英文单词
        if not result['function']:
            # 匹配独立英文单词（排除中文前缀）
            simple_func = re.search(r'\b([a-zA-Z_]\w*)\b', query)
            if simple_func:
                result['function'] = simple_func.group(1)
        
        # 提取源文件
        file_pattern = r'[\w./-]+\.py'
        file_match = re.search(file_pattern, query)
        if file_match:
            result['source_file'] = file_match.group(0)
        
        # 识别场景类型
        if any(kw in query.lower() for kw in ['异常', '错误', 'error', 'exception']):
            result['scenario_type'] = 'error_handling'
            result['test_points'].append('异常处理')
        elif any(kw in query.lower() for kw in ['边界', '边缘', 'edge', 'boundary']):
            result['scenario_type'] = 'edge_case'
            result['test_points'].append('边界值')
        
        # 识别优先级
        if any(kw in query.lower() for kw in ['高优先级', '重要', 'high', 'critical']):
            result['priority'] = 'high'
        elif any(kw in query.lower() for kw in ['低优先级', 'low', 'optional']):
            result['priority'] = 'low'
        
        # 提取显式测试点
        test_point_keywords = {
            '超时': '超时处理',
            'timeout': '超时处理',
            '重试': '重试机制',
            'retry': '重试机制',
            '权限': '权限验证',
            'permission': '权限验证',
            '并发': '并发处理',
            'concurrent': '并发处理'
        }
        for keyword, point in test_point_keywords.items():
            if keyword in query.lower():
                result['test_points'].append(point)
        
        return result

    def save_test_case(self, test_case, source_file):
        """保存测试用例到文件"""
        base_name = os.path.splitext(os.path.basename(source_file))[0]
        scenario_suffix = test_case.get('scenario', 'default')
        test_filename = f"test_{base_name}_{test_case['function_name']}_{scenario_suffix}.py"
        test_path = os.path.join(self.output_dir, test_filename)

        with open(test_path, 'w', encoding='utf-8') as f:
            f.write(test_case['test_code'])

        print(f"✓ 已生成: {test_path} [优先级:{test_case.get('priority', 'N/A')}]")
        return test_path
    
    def _register_custom_tools(self):
        """注册自定义工具"""
        def save_test_tool(content: str, filename: str) -> dict:
            """保存测试文件工具"""
            filepath = os.path.join(self.output_dir, filename)
            try:
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(content)
                return {'success': True, 'path': filepath}
            except Exception as e:
                return {'success': False, 'error': str(e)}
        
        self.tools.register(
            name='save_test',
            func=save_test_tool,
            description='保存测试代码到文件'
        )
    
    def _get_template(self, func_name):
        """获取模板代码（降级方案）"""
        from utils.template_loader import ClientTestTemplates
        
        if self.test_type == 'mobile':
            return ClientTestTemplates.appium_mobile_test(func_name)
        elif self.test_type == 'api':
            return ClientTestTemplates.api_client_test(func_name)
        elif 'Selenium' in str(self.test_type):
            return ClientTestTemplates.selenium_web_test(func_name)
        else:
            return ClientTestTemplates.playwright_web_test(func_name)

    def _build_prompt(self, scenario):
        """基于测试场景构建prompt"""
        framework_map = {
            'web': 'Playwright/Selenium',
            'mobile': 'Appium',
            'api': 'requests + pytest'
        }
        framework = framework_map.get(self.test_type, 'Playwright')
        
        test_points_str = '、'.join(scenario.get('test_points', []))
        
        # 使用通用模板
        return build_prompt(_DIR, 'generate_test.md',
            framework=framework,
            function_name=scenario['function'],
            scenario_type=scenario['scenario'],
            test_points=test_points_str,
            priority=scenario.get('priority', 'medium'),
            description=scenario['description'])

    # 每批最多测试点数（超过则在拆批阶段处理，这里 assert 兜底）
    _MAX_POINTS_PER_BATCH = 5

    def execute_batch(self, batch: dict, _depth: int = 0) -> dict:
        """按端 TestBatch 生成 JSON 测试用例（platform 路由到该端专用 prompt）
        若 LLM 输出被截断，自动将本 batch 拆成更小的子 batch 递归处理并合并结果。
        :param batch: {platform, platform_label, batch_index, shared_context, test_points, priority}
        :param _depth: 递归深度（防无限递归，最多 4 层）
        :return: {'test_cases': list, 'platform': str, 'batch_index': int, 'platform_label': str}
        """
        platform = batch.get('platform', 'common')
        sc = batch.get('shared_context') or {}
        pts = batch.get('test_points', [])

        # === 安全断言：每批 ≤ _MAX_POINTS_PER_BATCH，超过说明上游拆批有 bug ===
        if _depth == 0 and len(pts) > self._MAX_POINTS_PER_BATCH:
            logger.warning(
                f"batch 过大：{platform}-batch{batch.get('batch_index', 1)} 有 {len(pts)} 点，"
                f"超过上限 {self._MAX_POINTS_PER_BATCH}，自动拆分为子批"
            )
            mid = len(pts) // 2
            test_cases = []
            for sub_pts in (pts[:mid], pts[mid:]):
                sub_batch = dict(batch)
                sub_batch['test_points'] = sub_pts
                sub = self.execute_batch(sub_batch, _depth=_depth + 1)
                test_cases.extend(sub.get('test_cases', []))
            return {
                'test_cases': test_cases,
                'platform': platform,
                'batch_index': batch.get('batch_index', 1),
                'platform_label': batch.get('platform_label', platform),
            }

        # 测试点列表文本
        lines = []
        for p in pts:
            module = p.get('module', '')
            detail = p.get('detail', '')
            priority = p.get('priority', 'P1')
            line = f"- {detail}（优先级:{priority}）"
            if module:
                line = f"- 【{module}】{detail}（优先级:{priority}）"
            lines.append(line)
        test_points_list = '\n'.join(lines) or '（无）'
        prompt_file = _PROMPT_REGISTRY.get(platform, 'client_test.md')
        prompt = build_prompt(_DIR, prompt_file,
            platform_label=batch.get('platform_label', platform),
            framework=sc.get('framework', 'Playwright'),
            assertion_focus=sc.get('assertion_focus', ''),
            batch_index=batch.get('batch_index', 1),
            test_points_list=test_points_list,
            requirement_context=sc.get('requirement_context', '（无）'))
        test_cases = []
        if self.llm:
            try:
                logger.info(f"  ▶ [LLM调用] 测试用例-{platform_label} batch#{batch.get('batch_index', 1)}（{len(pts)}测试点, max_tokens=3500, depth={_depth}）")
                t0 = time.time()
                response = self.llm.generate(prompt, max_tokens=3500)
                logger.info(f"  ◀ [LLM返回] {platform_label} batch#{batch.get('batch_index', 1)} 耗时{time.time()-t0:.1f}s，输出{len(response or '')}字符")
            except Exception as e:
                logger.warning(f"  ✗ LLM 请求异常: {e}")
                response = ''
            test_cases = self._parse_json_response(response)
            # 截断 → 递归拆子批（最小粒度 2，再小就不拆了）
            if self._is_truncated(response) and len(pts) > 2 and _depth < 4:
                mid = len(pts) // 2
                left_pts, right_pts = pts[:mid], pts[mid:]
                logger.warning(
                    f"[递归{_depth+1}] {platform}-batch{batch.get('batch_index', 1)} "
                    f"被截断（{len(pts)}点→{len(test_cases)}条），拆为 {len(left_pts)}+{len(right_pts)} 子批"
                )
                test_cases = []
                for sub_pts in (left_pts, right_pts):
                    sub_batch = dict(batch)
                    sub_batch['test_points'] = sub_pts
                    sub_batch['batch_index'] = f"{batch.get('batch_index', 1)}.{_depth+1}"
                    sub = self.execute_batch(sub_batch, _depth=_depth + 1)
                    test_cases.extend(sub.get('test_cases', []))
            elif self._is_truncated(response) and not test_cases:
                logger.warning(
                    f"[截断但已到最小粒度] {platform}-batch{batch.get('batch_index', 1)}: 0条可提取"
                )
        else:
            response = '{"test_cases": []}'
            test_cases = self._parse_json_response(response)
        print(f"  ✓ [{platform} · 第{batch.get('batch_index', 1)}批] 生成 {len(test_cases)} 条用例")
        return {
            'test_cases': test_cases,
            'platform': platform,
            'batch_index': batch.get('batch_index', 1),
            'platform_label': batch.get('platform_label', platform),
        }

    @staticmethod
    def _is_truncated(response: str) -> bool:
        """检测 LLM 输出是否被截断"""
        # 1. 检查 ```json 代码块是否闭合
        if '```json' in response:
            # 若只开没关 → 截断
            open_cnt = response.count('```json')
            close_cnt = response.count('```') - open_cnt
            if close_cnt < open_cnt:
                return True
            # 若能解析到闭合块，尝试解析 JSON
            m = re.search(r'```json\s*\n(.*?)```', response, re.DOTALL)
            if m:
                try:
                    json.loads(m.group(1).strip())
                    return False
                except Exception:
                    return True
        # 2. 检查纯 JSON 是否能解析
        stripped = response.strip()
        if stripped.startswith('{') or stripped.startswith('['):
            try:
                json.loads(stripped)
                return False
            except Exception:
                return True
        # 3. 兜底：结尾未在正常断句位置
        tail = stripped[-30:] if stripped else ''
        if tail and tail[-1] not in '.。!！?？」』]\n':
            return True
        return False

    def _generate_cases_with_retry(self, prompt: str, initial_max_tokens: int = 3000,
                                   max_retries: int = 2) -> list:
        """带重试的弹性策略：若 LLM 输出被截断，翻倍 max_tokens 重试，最多 max_retries 次。
        每次重试都会先尝试用截断修复逻辑提取已有的完整用例，避免丢弃成功部分。
        """
        max_tokens = initial_max_tokens
        last_result = []
        for attempt in range(max_retries + 1):
            try:
                response = self.llm.generate(prompt, max_tokens=max_tokens)
            except Exception as e:
                logger.warning(f"[重试{attempt+1}/{max_retries}] LLM 请求异常: {e}")
                if attempt < max_retries:
                    max_tokens = min(max_tokens * 2, 8000)
                    continue
                raise
            cases = self._parse_json_response(response)
            if not self._is_truncated(response):
                # 未截断，直接返回
                return cases
            # 被截断：记录本次提取到的用例数（如果有），重试翻倍
            if cases:
                last_result = cases
            if attempt < max_retries:
                old_mt = max_tokens
                max_tokens = min(max_tokens * 2, 8000)
                logger.warning(
                    f"[重试{attempt+1}/{max_retries}] LLM 输出被截断（提取{len(cases)}条），"
                    f"max_tokens {old_mt}→{max_tokens} 重新生成"
                )
                continue
            # 全部重试仍截断，返回最后一次提取到的用例
            if last_result:
                logger.warning(f"所有重试仍截断，返回最后提取的 {len(last_result)} 条用例")
                return last_result
            return []
        return last_result

    def _parse_json_response(self, response: str) -> list:
        """解析 LLM 响应为 JSON 测试用例列表
        - 优先提取 ```json ``` 代码块
        - 降级：直接 json.loads 整段响应
        - 再降级：正则提取 {..."test_cases": [...]} 段
        - 终极兜底：尝试修复被截断的 JSON（去掉最后不完整的 test_case）
        """
        response = response.strip()
        # 1. 尝试提取 ```json 代码块
        json_text = None
        json_block = re.search(r'```json\s*\n(.*?)```', response, re.DOTALL)
        if json_block:
            json_text = json_block.group(1).strip()
        else:
            json_text = response

        # 尝试直接解析
        for attempt in (json_text,):
            try:
                data = json.loads(attempt)
                return data.get('test_cases', []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
            except json.JSONDecodeError:
                pass

        # 尝试正则提取完整 JSON
        brace_match = re.search(r'\{.*\}', json_text, re.DOTALL)
        if brace_match:
            try:
                data = json.loads(brace_match.group(0))
                return data.get('test_cases', []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
            except json.JSONDecodeError:
                pass

        # 终极兜底：修复被截断的 JSON
        # LLM 可能输出 {"test_cases": [case1, case2, {"test_module": "..."} (截断不完整)
        # 尝试补齐 test_cases 数组，去掉最后不完整的 case
        try:
            # 找到 test_cases 数组开始
            tc_start = json_text.find('"test_cases"')
            if tc_start == -1:
                logger.warning(f"JSON 解析失败（无 test_cases 键），返回空用例。响应前200字符: {response[:200]}")
                return []
            # 找到 test_cases 后的 [
            arr_start = json_text.find('[', tc_start)
            if arr_start == -1:
                logger.warning(f"JSON 解析失败（test_cases 后无数组），返回空用例。响应前200字符: {response[:200]}")
                return []
            # 从 arr_start 之后找最后一个完整的 test_case（以 } 结尾，且 } 后是 , 或 ] 或结尾）
            after_arr = json_text[arr_start + 1:]
            last_complete = -1
            depth = 0
            i = 0
            while i < len(after_arr):
                c = after_arr[i]
                if c == '{':
                    depth += 1
                elif c == '}':
                    depth -= 1
                    if depth == 0:
                        # 找到一个完整 case 的结尾 }
                        last_complete = i
                i += 1
            if last_complete >= 0:
                # 截取到这个完整的 }，然后补齐 ]}
                fixed_inner = after_arr[:last_complete + 1]
                fixed_json = json_text[:arr_start + 1] + fixed_inner + ']}'
                data = json.loads(fixed_json)
                cases = data.get('test_cases', [])
                if cases:
                    logger.warning(f"JSON 被截断但修复成功，提取 {len(cases)} 条完整用例")
                    return cases
        except (json.JSONDecodeError, Exception):
            pass

        logger.warning(f"LLM 响应解析为 JSON 失败，返回空用例列表。响应前200字符: {response[:200]}")
        return []

    @staticmethod
    def cases_to_feishu_struct(test_cases: list, platform_label: str = '') -> list:
        """将 JSON 测试用例列表转为飞书 struct_nodes（用于 create_doc_from_struct）
        对齐参考文档格式：H1=模块 → H2=测试点 → 每测试点下一个表格
        表格列：优先级 | 测试场景 | 测试步骤 | 预期结果 | 执行结果 | 备注
        """
        if not test_cases:
            return [{'type': 'paragraph', 'text': '（本批次无测试用例）'}]

        # 按 test_module → test_point 两级分组
        by_module: dict[str, dict[str, list]] = {}
        for tc in test_cases:
            mod = tc.get('test_module', '未分类')
            point = tc.get('test_point', tc.get('test_scenario', ''))
            by_module.setdefault(mod, {}).setdefault(point, []).append(tc)

        struct_nodes = []
        HEADERS = ['优先级', '测试场景', '测试步骤', '预期结果', '执行结果', '备注']

        for mod, points in by_module.items():
            struct_nodes.append({'type': 'h1', 'text': mod})
            for point, cases in points.items():
                struct_nodes.append({'type': 'h2', 'text': point})
                rows = []
                for tc in cases:
                    steps = '\n'.join(tc.get('test_steps', []))
                    expected = '\n'.join(tc.get('expected_results', []))
                    rows.append([
                        tc.get('priority', ''),
                        tc.get('test_scenario', ''),
                        steps,
                        expected,
                        '未执行',
                        tc.get('remarks', '无'),
                    ])
                struct_nodes.append({'type': 'table', 'headers': HEADERS, 'rows': rows})

        return struct_nodes

    def _parse_code_response(self, response: str) -> str:
        """旧链路兼容：解析 LLM 响应中的 Python 代码块（execute 方法使用）
        - 优先 ```python ``` 标记
        - 无标记时检测是否像 Python，像则直接返回
        """
        if '```python' in response:
            start = response.find('```python') + 9
            end = response.find('```', start)
            return response[start:end].strip()
        # 检测非 Python 语言标记
        lang_block = re.search(r'```(typescript|javascript|\bts\b|\bjs\b)\s*\n', response, re.IGNORECASE)
        if lang_block:
            raise ValueError(f"LLM返回了{lang_block.group(1)}代码，期望Python")
        # 无标记，检查内容是否像 Python
        stripped = response.strip()
        python_indicators = ['def test_', 'import pytest', 'from playwright.sync_api',
                             'from appium', 'import requests', 'import httpx', 'def ', 'import ']
        if any(ind in stripped for ind in python_indicators):
            return stripped
        raise ValueError("LLM响应未检测到Python代码块，无法解析")
