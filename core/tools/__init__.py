"""工具模块"""
from core.tools.registry import ToolRegistry
from core.tools.tool_defs.code_analyzer_tool import code_analyzer_tool
from core.tools.tool_defs.test_runner_tool import test_runner_tool
from core.tools.tool_defs.file_writer_tool import file_writer_tool


def create_default_tools() -> ToolRegistry:
    """创建默认工具集"""
    registry = ToolRegistry()
    
    registry.register(
        name='code_analyzer',
        func=code_analyzer_tool,
        description='分析Python代码结构，提取函数和类'
    )
    
    registry.register(
        name='test_runner',
        func=test_runner_tool,
        description='运行pytest测试并返回结果'
    )
    
    registry.register(
        name='file_writer',
        func=file_writer_tool,
        description='将内容写入文件'
    )
    
    return registry


__all__ = ['ToolRegistry', 'create_default_tools']
