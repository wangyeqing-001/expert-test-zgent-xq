"""任务规划器 - 分解复杂任务为步骤"""
from typing import List, Dict


class TaskPlan:
    """任务计划"""
    
    def __init__(self, steps: List[Dict]):
        self.steps = steps
        self.current_step = 0
        self.results = []
    
    def next_step(self) -> Dict:
        """获取下一步骤"""
        if self.current_step < len(self.steps):
            step = self.steps[self.current_step]
            self.current_step += 1
            return step
        return None
    
    def add_result(self, result: Dict):
        """记录步骤结果"""
        self.results.append(result)
    
    def finished(self) -> bool:
        """检查是否完成"""
        return self.current_step >= len(self.steps)
    
    def get_progress(self) -> str:
        """获取进度"""
        return f"{self.current_step}/{len(self.steps)}"


class Planner:
    """任务规划器"""
    
    def __init__(self):
        self.plans = {}
    
    def make_plan(self, task_type: str, context: Dict) -> TaskPlan:
        """根据任务类型生成计划"""
        
        if task_type == 'test_generation':
            return self._plan_test_generation(context)
        elif task_type == 'code_analysis':
            return self._plan_code_analysis(context)
        else:
            return self._plan_default(context)
    
    def _plan_test_generation(self, context: Dict) -> TaskPlan:
        """测试生成任务计划"""
        scenarios = context.get('scenarios', [])
        
        steps = []
        for i, scenario in enumerate(scenarios):
            steps.append({
                'step_id': i + 1,
                'action': 'generate_test',
                'scenario': scenario,
                'description': f"生成测试: {scenario['description']}"
            })
            
            # 可选：添加验证步骤
            steps.append({
                'step_id': i + 1.5,
                'action': 'validate_test',
                'scenario': scenario,
                'optional': True
            })
        
        return TaskPlan(steps)
    
    def _plan_code_analysis(self, context: Dict) -> TaskPlan:
        """代码分析任务计划"""
        return TaskPlan([
            {
                'step_id': 1,
                'action': 'analyze_structure',
                'description': '分析代码结构'
            },
            {
                'step_id': 2,
                'action': 'extract_requirements',
                'description': '提取测试需求'
            },
            {
                'step_id': 3,
                'action': 'assess_complexity',
                'description': '评估复杂度'
            }
        ])
    
    def _plan_default(self, context: Dict) -> TaskPlan:
        """默认计划"""
        return TaskPlan([
            {
                'step_id': 1,
                'action': 'process',
                'description': '处理任务',
                'context': context
            }
        ])
