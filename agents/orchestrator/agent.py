"""Agent编排器 - 协调测试生成流水线"""
from agents.requirement_analyzer import RequirementAnalyzer
from agents.test_point_generator import TestPointGenerator
from agents.test_generator import TestGeneratorAgent


class AgentOrchestrator:
    """Agent编排器：管理测试生成流水线（3步）
    
    Step1: RequirementAnalyzer  → 需求分析 + 写入飞书文档
    Step2: TestPointGenerator   → 生成测试场景/测试点
    Step3: TestGeneratorAgent   → 生成测试代码
    """
    
    def __init__(self, test_type='web', api_key=None, base_url=None, llm_client=None, feishu_client=None):
        self.test_type = test_type
        
        self.req_analyzer = RequirementAnalyzer(
            llm_client=llm_client,
            feishu_client=feishu_client
        )
        self.test_point_agent = TestPointGenerator(llm_client=llm_client, feishu_client=feishu_client)
        self.test_generator = TestGeneratorAgent(
            api_key=api_key,
            base_url=base_url,
            test_type=test_type
        )
        
        self.agents = {
            'requirement': self.req_analyzer,
            'test_point': self.test_point_agent,
            'generator': self.test_generator
        }
        
        self.workflow_log = []
    
    def execute_workflow(self, file_path: str, requirements: list = None, generate_all: bool = True):
        """
        执行测试生成流水线（3步）
        :param file_path: 目标文件路径
        :param requirements: 结构化需求列表（外部传入），无则走需求分析
        :param generate_all: 是否为所有场景生成测试
        :return: dict {'generated_files': list, 'requirement_result': dict}
        """
        print(f"\n{'='*50}")
        print(f"启动测试生成流水线（3步）")
        print(f"{'='*50}")
        
        # Step 1: 需求分析（写飞书 + 提取结构化需求）
        requirement_result = None
        if not requirements:
            print("\n[Step 1/3] 需求分析Agent正在分析代码...")
            requirement_result = self.req_analyzer.execute({
                'file_path': file_path,
                'test_type': self.test_type
            })
            self._log_step('RequirementAnalyzer', requirement_result.get('metadata', {}))
            print(f"✓ 需求分析完成: {requirement_result['local_path']}")
            if requirement_result.get('feishu_url'):
                print(f"✓ 飞书文档: {requirement_result['feishu_url']}")
            
            # 从markdown提取结构化需求传给下游
            requirements = self._extract_requirements(requirement_result['markdown'])
            print(f"✓ 提取 {len(requirements)} 条结构化需求传给下游")
        else:
            print("\n[Step 1/3] 跳过需求分析（外部已提供需求列表）")
        
        # Step 2: 测试点生成（产出表格文档：本地.md + 飞书）
        print("\n[Step 2/3] 测试点生成Agent正在生成测试场景...")
        req_meta = ((requirement_result or {}).get('metadata') or {}) if requirement_result else {}
        if req_meta.get('source') in ('prd_document', 'feishu_doc'):
            # PRD/飞书需求 → prd直提链路：主材料=原始文档全文（无则退回分析文）
            raw_prd = (requirement_result or {}).get('raw_content') \
                or (requirement_result or {}).get('markdown', '')
            point_requirements = [{
                'function': 'all', 'name': '需求文档', 'complexity': 'medium',
                'test_points': ['功能逻辑'],
                'description': raw_prd[:6000]
            }]
            point_source = 'prd'
        else:
            point_requirements = requirements or []
            point_source = 'code'
        point_input = {
            'requirements': point_requirements,
            'test_type': self.test_type,
            'source': point_source,
            'title': req_meta.get('title')
        }
        if point_source == 'prd':
            point_input['raw_prd'] = raw_prd
        point_result = self.test_point_agent.execute(point_input)
        
        scenarios = point_result['scenarios']
        self._log_step('TestPointGenerator', point_result['metadata'])
        print(f"✓ 生成 {len(scenarios)} 个测试场景")
        if point_result.get('test_points'):
            print(f"✓ 测试点 {len(point_result['test_points'])} 条, JSON: {point_result.get('test_points_json_path')}")
        if point_result.get('local_path'):
            print(f"✓ 测试点表格: {point_result['local_path']}")
        if point_result.get('feishu_url'):
            print(f"✓ 飞书测试点文档: {point_result['feishu_url']}")
        self._preview_scenarios(scenarios)
        
        # Step 3: 测试代码生成
        print(f"\n[Step 3/3] 测试生成Agent开始生成代码...")
        generated_files = []
        
        if not generate_all:
            scenarios = [s for s in scenarios if s.get('priority') == 'high']
            print(f"仅生成高优先级场景 ({len(scenarios)}个)")
        
        for i, scenario in enumerate(scenarios, 1):
            print(f"\n  [{i}/{len(scenarios)}] 生成: {scenario['description']}")
            
            try:
                result = self.test_generator.execute({
                    'scenario': scenario,
                    'source_file': file_path
                })
                generated_files.append(result['file_path'])
            except Exception as e:
                print(f"  ✗ 生成失败: {e}")
                self._log_step('TestGenerator', {'status': 'failed', 'error': str(e)})
        
        # 总结
        print(f"\n{'='*50}")
        print(f"✓ 流水线完成！共生成 {len(generated_files)} 个测试文件")
        print(f"输出目录: generated_tests/")
        if requirement_result and requirement_result.get('feishu_url'):
            print(f"飞书需求文档: {requirement_result['feishu_url']}")
        print(f"{'='*50}")
        
        return {
            'generated_files': generated_files,
            'requirement_result': requirement_result
        }
    
    @staticmethod
    def _extract_requirements(markdown: str) -> list:
        """从需求分析Markdown中提取结构化需求列表
        
        解析Markdown表格行: | `func_name` | params | complexity | test_points |
        """
        import re
        requirements = []
        
        # 匹配表格中的功能行: | `name` | params | complexity | test_points |
        table_pattern = r'\|\s*`(\w+)`\s*\|\s*([^|]+)\s*\|\s*(low|medium|high)\s*\|\s*([^|]+)\s*\|'
        for match in re.finditer(table_pattern, markdown):
            func_name = match.group(1)
            params = match.group(2).strip()
            complexity = match.group(3).strip()
            test_points = [tp.strip() for tp in match.group(4).split(',') if tp.strip()]
            
            requirements.append({
                'function': func_name,
                'name': func_name,
                'params': params,
                'complexity': complexity,
                'test_points': test_points or ['功能逻辑']
            })
        
        # 如果没匹配到表格行，将整段markdown作为单条需求
        if not requirements:
            requirements.append({
                'function': 'all',
                'name': '需求文档',
                'complexity': 'medium',
                'test_points': ['功能逻辑'],
                'description': markdown[:500]
            })
        
        return requirements
    
    def get_agent_status(self):
        """获取所有Agent状态"""
        return {
            name: agent.get_state()
            for name, agent in self.agents.items()
        }
    
    def reset_all(self):
        """重置所有Agent"""
        for agent in self.agents.values():
            agent.reset()
        self.workflow_log.clear()
    
    def _log_step(self, agent_name, result):
        self.workflow_log.append({'agent': agent_name, 'result': result})
    
    def _preview_scenarios(self, scenarios):
        print(f"\n测试场景预览:")
        high_priority = [s for s in scenarios if s.get('priority') == 'high']
        medium_priority = [s for s in scenarios if s.get('priority') == 'medium']
        
        print(f"  • 高优先级: {len(high_priority)}个")
        for s in high_priority[:3]:
            print(f"    - {s['description']}")
        if len(high_priority) > 3:
            print(f"    ... 还有{len(high_priority)-3}个")
        
        print(f"  • 中优先级: {len(medium_priority)}个")
