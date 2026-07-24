"""工具注册中心 - 管理Agent可调用的工具"""
from typing import Callable, Dict, Any


class ToolRegistry:
    """工具注册与管理"""
    
    def __init__(self):
        self.tools = {}
    
    def register(self, name: str, func: Callable, description: str = ""):
        """注册工具"""
        if not callable(func):
            raise ValueError(f"工具 {name} 必须是可调用对象")
        
        self.tools[name] = {
            'func': func,
            'description': description
        }
    
    def execute(self, tool_name: str, **kwargs) -> Any:
        """执行工具"""
        if tool_name not in self.tools:
            available = list(self.tools.keys())
            raise ValueError(f"未知工具: {tool_name}, 可用工具: {available}")
        
        tool = self.tools[tool_name]
        return tool['func'](**kwargs)
    
    def list_tools(self) -> Dict[str, str]:
        """列出所有可用工具"""
        return {
            name: info['description']
            for name, info in self.tools.items()
        }
    
    def has_tool(self, name: str) -> bool:
        """检查工具是否存在"""
        return name in self.tools
    
    def unregister(self, name: str):
        """注销工具"""
        if name in self.tools:
            del self.tools[name]
