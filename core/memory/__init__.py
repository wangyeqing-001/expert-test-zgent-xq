"""记忆模块"""
from core.memory.base_memory import BaseMemory
from core.memory.working_memory import WorkingMemory
from core.memory.long_memory import LongTermMemory


class Memory:
    """Agent记忆系统（组合工作记忆+长期记忆）"""
    
    def __init__(self, agent_name: str):
        self.working = WorkingMemory()
        self.long_term = LongTermMemory(agent_name)
    
    def add_working(self, data):
        """添加到工作记忆"""
        self.working.save(data)
    
    def get_working(self):
        """获取工作记忆"""
        return self.working.load()
    
    def clear_working(self):
        """清空工作记忆"""
        self.working.clear()
    
    def save_experience(self, experience):
        """保存经验到长期记忆"""
        self.long_term.save(experience)
    
    def load_long_term(self, query: str = None):
        """加载长期记忆"""
        return self.long_term.load(query)
    
    def get_summary(self):
        """获取记忆摘要"""
        return {
            'working_steps': len(self.working.load()),
            'long_term_keys': list(self.long_term.data.keys()),
            'experience_count': len(self.long_term.experience_log)
        }
