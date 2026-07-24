"""文件写入工具"""


def file_writer_tool(file_path: str, content: str) -> dict:
    """文件写入工具"""
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return {'success': True, 'path': file_path}
    except Exception as e:
        return {'success': False, 'error': str(e)}
