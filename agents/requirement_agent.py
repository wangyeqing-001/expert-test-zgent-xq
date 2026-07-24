"""需求分析Agent - 解析业务需求和测试范围"""
from agents.base_agent import BaseAgent
from utils.code_analyzer import CodeAnalyzer


class RequirementAnalyzer(BaseAgent):
    """需求分析Agent：从代码中提取测试需求"""
    
    def __init__(self, llm_client=None):
        super().__init__(name="RequirementAnalyzer", llm_client=llm_client)
    
    def execute(self, input_data: dict) -> dict:
        """
        分析代码并生成测试需求文档
        :param input_data: {'file_path': str, 'test_type': str}
        :return: {'requirements': list, 'test_scenarios': list}
        """
        if not self.validate_input(input_data):
            raise ValueError("Invalid input: file_path and test_type required")
        
        file_path = input_data['file_path']
        test_type = input_data.get('test_type', 'web')
        
        # 1. 代码结构分析
        analyzer = CodeAnalyzer(file_path)
        functions = analyzer.extract_functions()
        classes = analyzer.extract_classes()
        code_content = analyzer.get_code_content()  # 获取完整代码
        
        # 2. 用LLM提取测试需求（替代规则匹配）
        requirements = self._extract_requirements_with_llm(code_content, functions, test_type)
        
        # 3. 用LLM生成测试场景（替代模板化生成）
        test_scenarios = self._generate_scenarios_with_llm(requirements, test_type)
        
        self.state = {
            'analyzed_file': file_path,
            'function_count': len(functions),
            'class_count': len(classes),
            'requirement_count': len(requirements)
        }
        
        return {
            'requirements': requirements,
            'test_scenarios': test_scenarios,
            'metadata': self.state
        }
    
    def validate_input(self, input_data: any) -> bool:
        if not isinstance(input_data, dict):
            return False
        return 'file_path' in input_data
    
    def _extract_requirements_with_llm(self, code_content, functions, test_type):
        """用LLM从代码中提取测试需求"""
        if not self.llm:
            # 降级方案：使用规则提取
            return self._extract_requirements(functions, [], test_type)
        
        # 构建prompt
        prompt = f"""你是资深测试工程师。分析以下代码，识别需要测试的功能点。

代码类型: {test_type}

代码内容:
{code_content}

要求：
1. 识别每个函数的业务意图
2. 分析参数验证需求
3. 根据代码类型识别特定测试点（UI交互/手势操作/API调用等）
4. 评估复杂度（low/medium/high）
5. 返回JSON格式：{{"functions": [{{"name": "", "params": [], "complexity": "", "test_points": []}}]}}

只返回JSON，无解释。"""
        
        try:
            response = self.llm.generate(prompt)
            import json
            result = json.loads(response)
            return result.get('functions', [])
        except Exception as e:
            print(f"LLM分析失败，降级到规则提取: {e}")
            return self._extract_requirements(functions, [], test_type)
    
    def _extract_requirements(self, functions, classes, test_type):
        """从代码中提取测试需求（降级方案）"""
        requirements = []
        
        for func in functions:
            req = {
                'function': func['name'],
                'params': func['params'],
                'complexity': self._assess_complexity(func),
                'test_points': self._identify_test_points(func, test_type)
            }
            requirements.append(req)
        
        return requirements
    
    def _assess_complexity(self, func_info):
        """评估函数复杂度"""
        code_lines = func_info['code'].count('\n') + 1
        param_count = len(func_info['params'])
        
        if code_lines > 50 or param_count > 5:
            return 'high'
        elif code_lines > 20 or param_count > 3:
            return 'medium'
        return 'low'
    
    def _identify_test_points(self, func_info, test_type):
        """识别测试点"""
        test_points = []
        code = func_info['code'].lower()
        
        # 通用测试点
        if func_info['params']:
            test_points.append('参数验证')
        
        # 根据测试类型添加特定测试点
        if test_type == 'web':
            if any(kw in code for kw in ['click', 'button', 'submit']):
                test_points.append('UI交互')
            if any(kw in code for kw in ['navigate', 'goto', 'redirect']):
                test_points.append('页面导航')
        
        elif test_type == 'mobile':
            if any(kw in code for kw in ['swipe', 'scroll', 'gesture']):
                test_points.append('手势操作')
            if any(kw in code for kw in ['screen', 'display', 'orientation']):
                test_points.append('屏幕适配')
        
        elif test_type == 'api':
            if any(kw in code for kw in ['request', 'http', 'fetch']):
                test_points.append('HTTP调用')
            if any(kw in code for kw in ['timeout', 'retry']):
                test_points.append('超时处理')
        
        if not test_points:
            test_points.append('功能逻辑')
        
        return test_points
    
    def _generate_scenarios_with_llm(self, requirements, test_type):
        """用LLM生成测试场景列表"""
        if not self.llm:
            # 降级方案：使用模板生成
            return self._generate_scenarios(requirements, test_type)
        
        prompt = f"""你是测试专家。根据以下需求生成测试场景。

测试类型: {test_type}
需求列表:
{requirements}

要求：
1. 为每个功能生成正常、边界、异常三类场景
2. 根据复杂度调整场景数量
3. 描述具体测试步骤
4. 设置优先级（high/medium/low）
5. 返回JSON格式：{{"scenarios": [{{"function": "", "scenario": "", "description": "", "priority": "", "test_points": []}}]}}

只返回JSON，无解释。"""
        
        try:
            response = self.llm.generate(prompt)
            import json
            result = json.loads(response)
            return result.get('scenarios', [])
        except Exception as e:
            print(f"LLM生成场景失败，降级到模板生成: {e}")
            return self._generate_scenarios(requirements, test_type)
        """生成测试场景列表"""
        scenarios = []
        
        for req in requirements:
            # 正常场景
            scenarios.append({
                'function': req['function'],
                'scenario': 'normal',
                'description': f"测试{req['function']}正常流程",
                'priority': 'high',
                'test_points': req['test_points']
            })
            
            # 边界场景（仅对复杂函数）
            if req['complexity'] in ['medium', 'high']:
                scenarios.append({
                    'function': req['function'],
                    'scenario': 'edge_case',
                    'description': f"测试{req['function']}边界条件",
                    'priority': 'medium',
                    'test_points': ['边界值', '异常输入']
                })
            
            # 异常场景
            scenarios.append({
                'function': req['function'],
                'scenario': 'error_handling',
                'description': f"测试{req['function']}异常处理",
                'priority': 'high',
                'test_points': ['错误恢复', '降级处理']
            })
        
        return scenarios
