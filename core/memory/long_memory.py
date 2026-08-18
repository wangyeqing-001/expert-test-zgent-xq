"""长期记忆 - 持久化经验存储"""
import json
import os
from typing import Any, Dict
from core.memory.base_memory import BaseMemory


class LongTermMemory(BaseMemory):
    """长期记忆（JSON文件持久化）"""
    
    def __init__(self, agent_name: str):
        self.agent_name = agent_name
        # 记忆文件存储在可配置目录，默认 memory_data/
        memory_dir = os.getenv('MEMORY_DIR', 'memory_data')
        os.makedirs(memory_dir, exist_ok=True)
        self.memory_file = os.path.join(memory_dir, f"memory_{agent_name}.json")
        self.data = {}
        self.experience_log = []
        
        # 加载已有数据
        self._load_from_file()
    
    def save(self, data: Dict) -> None:
        """保存经验到长期记忆"""
        self.experience_log.append(data)
        
        # 提取关键经验
        if 'success' in data:
            key = f"task_{data.get('task_type', 'unknown')}"
            if key not in self.data:
                self.data[key] = []
            
            self.data[key].append({
                'result': data['success'],
                'timestamp': data.get('timestamp'),
                'details': data.get('details', '')
            })
            
            # 持久化
            self._save_to_file()
    
    def load(self, query: str = None) -> Dict:
        """加载相关长期记忆"""
        if not query:
            return self.data
        
        # 关键词匹配
        relevant = {}
        for key, memories in self.data.items():
            if any(word in query.lower() for word in key.split('_')):
                relevant[key] = memories[-3:]  # 最近3条
        
        return relevant
    
    def clear(self) -> None:
        """清空长期记忆"""
        self.data.clear()
        self.experience_log.clear()
        if os.path.exists(self.memory_file):
            os.remove(self.memory_file)
    
    def _load_from_file(self):
        """从文件加载"""
        if os.path.exists(self.memory_file):
            try:
                with open(self.memory_file, 'r', encoding='utf-8') as f:
                    self.data = json.load(f)
            except Exception:
                self.data = {}
    
    def _save_to_file(self):
        """保存到文件"""
        try:
            with open(self.memory_file, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"⚠ 保存记忆失败: {e}")
