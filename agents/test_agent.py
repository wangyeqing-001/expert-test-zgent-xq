"""测试生成Agent - 根据需求生成测试代码（ReAct架构）"""
import os
from datetime import datetime
from agents.base_agent import BaseAgent
from core.llm_client import LLMClient
from core.tools import create_default_tools


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

    def validate_input(self, input_data: any) -> bool:
        """验证输入数据"""
        if not isinstance(input_data, dict):
            return False
        return 'scenario' in input_data and 'source_file' in input_data

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
        
        return f"""你是资深客户端测试工程师。根据以下需求生成{framework}测试代码。

功能: {scenario['function']}
场景类型: {scenario['scenario']}
测试点: {test_points_str}
优先级: {scenario.get('priority', 'medium')}

要求:
1. 使用{framework}框架
2. 覆盖场景: {scenario['description']}
3. 重点测试: {test_points_str}
4. 包含断言和异常处理
5. 添加必要的注释

只返回测试代码，无解释。"""

    def _parse_response(self, response):
        """解析LLM响应，提取代码块"""
        if '```python' in response:
            start = response.find('```python') + 9
            end = response.find('```', start)
            return response[start:end].strip()
        return response.strip()
