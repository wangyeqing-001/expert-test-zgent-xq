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
    """加载md模板并替换占位符。调用方自行截断字段值。
    若 kwargs 中有模板里不存在的占位符，静默跳过（不报错），
    但会告警（logger.warning）提醒调用方检查。
    """
    import logging
    logger = logging.getLogger(__name__)
    template = load_prompt(directory, filename)
    for key, value in kwargs.items():
        placeholder = '{' + key + '}'
        if placeholder not in template:
            # 静默跳过，不报错——有些 prompt 不需要某些可选字段
            logger.debug(f"{filename}: 跳过不存在的占位符 {placeholder}")
            continue
        template = template.replace(placeholder, str(value))
    return template
