"""单元测试：结构化JSON解析校验层（core/structured_doc.py）
覆盖：正确样例、```json代码块包裹、末尾多逗号、table行列不匹配、未知type
运行: .venv/bin/python -m pytest tests/test_struct_doc.py -v
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.structured_doc import is_struct_json, parse_struct_json, struct_to_markdown


class TestParseValid:
    """正确样例：纯JSON数组，无代码块、无多余文字"""

    def test_sample_from_spec(self):
        raw = '''[
  {"type":"h1","text":"需求全景"},
  {"type":"h2","text":"业务背景与价值"},
  {"type":"paragraph","text":"解决用户查询慢的痛点，提升查询成功率"},
  {"type":"bullet_list","items":["用户痛点1","业务指标：查询成功率提升3%"]},
  {"type":"table","headers":["模块","优先级"],"rows":[["查询模块","P0"]]}
]'''
        assert is_struct_json(raw)
        nodes = parse_struct_json(raw)
        assert nodes is not None
        assert [n['type'] for n in nodes] == ['h1', 'h2', 'paragraph', 'bullet_list', 'table']
        assert nodes[4]['headers'] == ['模块', '优先级']
        assert nodes[4]['rows'] == [['查询模块', 'P0']]

    def test_all_seven_types(self):
        raw = '''[
  {"type":"h1","text":"一级"},
  {"type":"h2","text":"二级"},
  {"type":"h3","text":"三级"},
  {"type":"paragraph","text":"段落"},
  {"type":"bullet_list","items":["a","b"]},
  {"type":"ordered_list","items":["步骤1","步骤2"]},
  {"type":"table","headers":["列A","列B"],"rows":[["1","2"]]}
]'''
        nodes = parse_struct_json(raw)
        assert nodes and len(nodes) == 7

    def test_markdown_render(self):
        nodes = parse_struct_json('[{"type":"h1","text":"标题"},{"type":"table","headers":["A"],"rows":[["1"]]}]')
        md = struct_to_markdown(nodes)
        assert '# 标题' in md
        assert '| A |' in md and '| :--- |' in md and '| 1 |' in md


class TestDirtyJsonInterception:
    """错误情况拦截/容错"""

    def test_code_fence_wrapped(self):
        """输出带```json代码块 → 去标记后正常解析"""
        raw = '```json\n[{"type":"h1","text":"标题"}]\n```'
        nodes = parse_struct_json(raw)
        assert nodes is not None and nodes[0]['text'] == '标题'

    def test_trailing_comma(self):
        """JSON末尾多逗号 → 标准json.loads失败，截取数组区间也失败 → 返回None触发降级"""
        raw = '[{"type":"h1","text":"标题"},]'
        # json.loads 对尾逗号报错；本实现不抛异常，返回None由上层降级
        nodes = parse_struct_json(raw)
        assert nodes is None

    def test_table_headers_rows_mismatch(self):
        """table headers长度与rows单元格数量不一致 → 自动补齐/截断（告警不中断）"""
        raw = '[{"type":"table","headers":["A","B","C"],"rows":[["1","2"],["a","b","c","d"]]}]'
        nodes = parse_struct_json(raw)
        assert nodes is not None
        rows = nodes[0]['rows']
        assert rows[0] == ['1', '2', '']        # 不足补齐
        assert rows[1] == ['a', 'b', 'c']       # 超出截断

    def test_unknown_type_filtered(self):
        """未定义type（如h4）→ 过滤跳过，不中断整体解析"""
        raw = '[{"type":"h1","text":"标题"},{"type":"h4","text":"非法"},{"type":"paragraph","text":"正文"}]'
        nodes = parse_struct_json(raw)
        assert nodes is not None
        assert [n['type'] for n in nodes] == ['h1', 'paragraph']

    def test_explanatory_text_around_json(self):
        """LLM前后带解释文字 → 截取[ ]区间解析"""
        raw = '以下是分析结果：\n[{"type":"h1","text":"标题"}]\n如有疑问请联系。'
        nodes = parse_struct_json(raw)
        assert nodes is not None and nodes[0]['text'] == '标题'

    def test_garbage_returns_none(self):
        """完全非JSON → None（上层降级，绝不喂给旧解析器）"""
        assert parse_struct_json('这是一段markdown\n## 标题') is None
        assert parse_struct_json('[]') is None

    def test_cell_newline_flattened(self):
        """单元格内部换行 → 替换为空格"""
        raw = '[{"type":"table","headers":["A"],"rows":[["第一行\\n第二行"]]}]'
        nodes = parse_struct_json(raw)
        assert nodes[0]['rows'][0] == ['第一行 第二行']


if __name__ == '__main__':
    import pytest
    sys.exit(pytest.main([__file__, '-v']))
