"""代码分析器 - 提取函数和类信息"""
import ast


class CodeAnalyzer:
    def __init__(self, file_path):
        self.file_path = file_path
        with open(file_path, 'r', encoding='utf-8') as f:
            self.source = f.read()
        self.tree = ast.parse(self.source)

    def extract_functions(self):
        """提取所有函数及其信息"""
        functions = []
        for node in ast.walk(self.tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                func_info = {
                    'name': node.name,
                    'params': [arg.arg for arg in node.args.args if arg.arg != 'self'],
                    'code': ast.get_source_segment(self.source, node) or '',
                    'line': node.lineno,
                    'docstring': ast.get_docstring(node) or ''
                }
                if node.returns:
                    func_info['return_type'] = ast.unparse(node.returns)
                functions.append(func_info)
        return functions

    def extract_classes(self):
        """提取所有类及其方法"""
        classes = []
        for node in ast.iter_child_nodes(self.tree):
            if isinstance(node, ast.ClassDef):
                class_info = {
                    'name': node.name,
                    'methods': [],
                    'bases': [ast.unparse(base) for base in node.bases]
                }
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        method_info = {
                            'name': item.name,
                            'params': [arg.arg for arg in item.args.args if arg.arg != 'self'],
                            'code': ast.get_source_segment(self.source, item) or ''
                        }
                        class_info['methods'].append(method_info)
                classes.append(class_info)
        return classes
    
    def get_code_content(self):
        """获取完整代码内容"""
        return self.source
