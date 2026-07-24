"""Agent编排器 - 协调多Agent协作"""
from agents.requirement_agent import RequirementAnalyzer
from agents.test_agent import TestGeneratorAgent


class AgentOrchestrator:
    """Agent编排器：管理多Agent工作流"""
    
    def __init__(self, test_type='web', api_key=None):
        self.test_type = test_type
        
        # 初始化各Agent（传入LLM客户端）
        self.requirement_agent = RequirementAnalyzer(llm_client=api_key)
        self.test_generator = TestGeneratorAgent(
            api_key=api_key,
            test_type=test_type
        )
        
        self.agents = {
            'requirement': self.requirement_agent,
            'generator': self.test_generator
        }
        
        self.workflow_log = []
    
    def execute_workflow(self, file_path: str, generate_all: bool = True):
        """
        执行完整的测试生成工作流
        :param file_path: 目标文件路径
        :param generate_all: 是否为所有场景生成测试
        :return: 生成的测试文件列表
        """
        print(f"\n{'='*50}")
        print(f"启动多Agent协作工作流")
        print(f"{'='*50}")
        
        # Step 1: 需求分析Agent工作
        print("\n[Step 1] 需求分析Agent正在分析代码...")
        requirement_result = self.requirement_agent.execute({
            'file_path': file_path,
            'test_type': self.test_type
        })
        
        self._log_step('RequirementAnalyzer', requirement_result['metadata'])
        
        print(f"✓ 识别到 {len(requirement_result['requirements'])} 个功能点")
        print(f"✓ 生成 {len(requirement_result['test_scenarios'])} 个测试场景")
        
        # Step 2: 展示测试结果预览
        self._preview_scenarios(requirement_result['test_scenarios'])
        
        # Step 3: 测试生成Agent工作
        print(f"\n[Step 2] 测试生成Agent开始生成代码...")
        generated_files = []
        
        scenarios = requirement_result['test_scenarios']
        if not generate_all:
            # 只生成高优先级场景
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
        
        # Step 4: 总结
        print(f"\n{'='*50}")
        print(f"✓ 工作流完成！共生成 {len(generated_files)} 个测试文件")
        print(f"输出目录: generated_tests/")
        print(f"{'='*50}")
        
        return generated_files
    
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
        """记录工作流步骤"""
        self.workflow_log.append({
            'agent': agent_name,
            'result': result
        })
    
    def _preview_scenarios(self, scenarios):
        """预览测试场景"""
        print(f"\n测试场景预览:")
        
        # 按优先级分组
        high_priority = [s for s in scenarios if s['priority'] == 'high']
        medium_priority = [s for s in scenarios if s['priority'] == 'medium']
        
        print(f"  • 高优先级: {len(high_priority)}个")
        for s in high_priority[:3]:  # 只显示前3个
            print(f"    - {s['description']}")
        
        if len(high_priority) > 3:
            print(f"    ... 还有{len(high_priority)-3}个")
        
        print(f"  • 中优先级: {len(medium_priority)}个")
