"""工作记忆 - 短期任务上下文"""
from typing import Any, Dict, List
from core.memory.base_memory import BaseMemory


class WorkingMemory(BaseMemory):
    """短期工作记忆（内存存储）"""
    
    def __init__(self):
        self.memory = []
    
    def save(self, data: Any) -> None:
        """添加到工作记忆"""
        self.memory.append({
            'step': len(self.memory) + 1,
            'data': data
        })
    
    def load(self, query: str = None) -> List[Dict]:
        """获取全部工作记忆"""
        return self.memory.copy()
    
    def clear(self) -> None:
        """清空工作记忆"""
        self.memory.clear()
    
    def get_last(self, n: int = 5) -> List[Dict]:
        """获取最近n条记录"""
        return self.memory[-n:] if self.memory else []
