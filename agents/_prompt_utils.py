"""Prompt模板加载工具 - 从.md文件加载prompt并注入参数"""
import os


def load_prompt(directory: str, filename: str) -> str:
    """加载同目录下的prompt .md文件（首次加载后缓存）"""
    path = os.path.join(directory, filename)
    if not hasattr(load_prompt, '_cache'):
        load_prompt._cache = {}
    if path not in load_prompt._cache:
        if not os.path.exists(path):
            raise FileNotFoundError(f"Prompt文件不存在: {path}")
        with open(path, 'r', encoding='utf-8') as f:
            load_prompt._cache[path] = f.read()
    return load_prompt._cache[path]


def build_prompt(directory: str, filename: str, **kwargs) -> str:
    """加载md模板并替换占位符。调用方自行截断字段值。"""
    template = load_prompt(directory, filename)
    for key, value in kwargs.items():
        placeholder = '{' + key + '}'
        if placeholder not in template:
            raise ValueError(f"{filename} 缺少 {placeholder} 占位符")
        template = template.replace(placeholder, str(value))
    return template
