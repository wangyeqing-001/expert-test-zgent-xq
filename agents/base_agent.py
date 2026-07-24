"""Agent基类 - 定义统一接口（完整ReAct架构）"""
from abc import ABC, abstractmethod
from typing import Any, Dict
from core.memory import Memory
from core.tools import ToolRegistry
from core.planner import Planner
from core.reflector import Reflector


class BaseAgent(ABC):
    """标准Agent架构：大脑+记忆+工具+规划+反思"""
    
    def __init__(self, name: str, llm_client=None):
        self.name = name
        self.llm = llm_client                # 大脑
        self.memory = Memory(name)           # 记忆模块
        self.tools = ToolRegistry()          # 工具库
        self.planner = Planner()             # 任务规划
        self.reflector = Reflector()         # 反思校验
        self.state = {}
    
    @abstractmethod
    def execute(self, input_data: Any) -> Any:
        """执行Agent核心逻辑（ReAct循环）"""
        pass
    
    @abstractmethod
    def validate_input(self, input_data: Any) -> bool:
        """验证输入数据"""
        pass
    
    def run(self, user_query: str, context: Dict = None) -> Any:
        """
        标准Agent运行流程
        :param user_query: 用户查询
        :param context: 上下文信息
        :return: 最终结果
        """
        # 1. 加载长期记忆
        relevant_memories = self.memory.load_long_term(user_query)
        
        # 2. 制定计划
        task_type = context.get('task_type', 'default') if context else 'default'
        plan = self.planner.make_plan(task_type, context or {})
        
        # 3. ReAct循环
        final_result = None
        while not plan.finished():
            step = plan.next_step()
            if not step:
                break
            
            # LLM思考下一步动作
            action = self._think(step, self.memory.get_working(), relevant_memories)
            
            # 执行动作
            if action.get('action_type') == 'call_tool':
                tool_name = action['tool']
                result = self.tools.execute(tool_name, **action.get('params', {}))
                self.memory.add_working({'step': step, 'result': result})
                plan.add_result(result)
            else:
                final_result = action.get('content')
        
        # 4. 反思评估
        if final_result:
            evaluation = self.reflector.evaluate(final_result)
            self.memory.save_experience({
                'task_type': task_type,
                'success': evaluation['passed'],
                'timestamp': str(__import__('datetime').datetime.now()),
                'details': evaluation.get('issues', [])
            })
        
        return final_result
    
    def _think(self, step: Dict, working_memory: list, long_term_memory: Dict) -> Dict:
        """LLM思考决策（真正调用LLM）"""
        if not self.llm:
            # 降级方案：直接返回步骤定义
            return {
                'action_type': 'call_tool' if 'action' in step else 'response',
                'tool': step.get('action'),
                'content': step.get('description')
            }
        
        # 构建上下文
        context = {
            'current_step': step,
            'working_memory': working_memory[-3:] if working_memory else [],  # 最近3条
            'long_term_memory': long_term_memory.get('relevant', []) if long_term_memory else []
        }
        
        prompt = f"""你是智能Agent。根据当前任务和记忆，决定下一步动作。

当前步骤: {step}
工作记忆: {context['working_memory']}
历史经验: {context['long_term_memory']}

要求：
1. 分析是否需要调用工具
2. 如需调用，指定工具名和参数
3. 如已完成，返回最终结果
4. 返回JSON格式：{{"action_type": "call_tool/response", "tool": "", "params": {{}}, "content": ""}}

只返回JSON，无解释。"""
        
        try:
            response = self.llm.generate(prompt)
            import json
            action = json.loads(response)
            return action
        except Exception as e:
            print(f"LLM思考失败，使用默认动作: {e}")
            return {
                'action_type': 'call_tool' if 'action' in step else 'response',
                'tool': step.get('action'),
                'content': step.get('description')
            }
    
    def get_state(self) -> Dict:
        """获取Agent状态"""
        return {
            'name': self.name,
            'memory': self.memory.get_summary(),
            'tools': list(self.tools.list_tools().keys()),
            'reflector': self.reflector.get_summary()
        }
    
    def reset(self):
        """重置Agent状态"""
        self.memory.clear_working()
        self.state.clear()
    
    def __repr__(self):
        return f"{self.__class__.__name__}(name={self.name})"
