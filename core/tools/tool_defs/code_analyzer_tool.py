"""代码分析工具"""
from utils.code_analyzer import CodeAnalyzer


def code_analyzer_tool(file_path: str) -> dict:
    """代码分析工具"""
    analyzer = CodeAnalyzer(file_path)
    return {
        'functions': analyzer.extract_functions(),
        'classes': analyzer.extract_classes()
    }
