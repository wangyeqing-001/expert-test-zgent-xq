"""记忆模块基类"""
from abc import ABC, abstractmethod
from typing import Any, Dict


class BaseMemory(ABC):
    """记忆系统基类"""
    
    @abstractmethod
    def save(self, data: Any) -> None:
        """保存数据"""
        pass
    
    @abstractmethod
    def load(self, query: str = None) -> Any:
        """加载数据"""
        pass
    
    @abstractmethod
    def clear(self) -> None:
        """清空记忆"""
        pass
