"""测试生成Agent - 根据需求生成测试代码（ReAct架构）"""
import os
from typing import Any
from datetime import datetime
from agents.base_agent import BaseAgent
from agents._prompt_utils import build_prompt
from core.llm_client import LLMClient
from core.tools import create_default_tools

_DIR = os.path.dirname(os.path.abspath(__file__))


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
        
        test_code = self._parse_response(response)
        
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

    def _parse_response(self, response):
        """解析LLM响应，提取代码块"""
        if '```python' in response:
            start = response.find('```python') + 9
            end = response.find('```', start)
            return response[start:end].strip()
        return response.strip()
